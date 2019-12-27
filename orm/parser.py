#!/usr/bin/env python3
import os
import glob
import re
import copy

import yaml


class ORMInternalParserException(Exception):
    pass


class ORMParserException(Exception):
    pass


def list_rules_files(rulesglob, recursive=True):
    """ Returns a list of yaml file paths """
    paths = glob.iglob(rulesglob, recursive=recursive)
    file_glob = filter(os.path.isfile, paths)
    return sorted(list(file_glob))


def parse_yaml_file(path):
    """ Returns an list of objects containing all YAML docs in the
        file at given path """
    stream = open(path, "r")
    yml_docs = list(yaml.safe_load_all(stream))
    stream.close()
    return yml_docs


scheme_delim = r"://"
port_delim = r":"


def extract_from_origin(origin):
    if scheme_delim in origin:
        scheme, hostport = origin.split(scheme_delim)
    else:
        scheme = "https"
        hostport = origin
    if port_delim in hostport:
        host, port = hostport.split(port_delim)
    else:
        host = hostport
        if scheme and scheme == "http":
            port = "80"
        else:
            port = "443"
    return (scheme, host, port)


def is_negation(expr):
    return "not" in expr and expr["not"]


def has_ignore_case(expr):
    return "ignore_case" in expr and expr["ignore_case"]


def create_match_tree_expr(src, func, inp):
    return {"match": {"source": src, "function": func, "input": inp}}


def parse_match_values(values_config, value_type):
    expr_list = []
    match_functions = ["exact", "regex", "begins_with", "ends_with", "contains"]
    options = ["not", "ignore_case"]
    ignore_case = has_ignore_case(values_config)
    for match_function in match_functions:
        if match_function in values_config:
            for value in values_config[match_function]:
                inp = {"value": value}
                if ignore_case:
                    inp["ignore_case"] = ignore_case
                expr_list.append(
                    create_match_tree_expr(value_type, match_function, inp)
                )
    for key in values_config.keys():
        if key not in match_functions and key not in options:
            raise ORMInternalParserException("ERROR: unhandled key : " + key)
    tree = {"or": expr_list}
    return {"not": tree} if is_negation(values_config) else tree


def parse_match_keyed_values(keyed_values_config, value_type, value_key):
    expr_list = []
    match_functions = [
        "exists",
        "exact",
        "regex",
        "begins_with",
        "ends_with",
        "contains",
    ]
    options = [value_key, "not", "ignore_case"]
    ignore_case = has_ignore_case(keyed_values_config)
    for match_function in match_functions:
        if match_function in keyed_values_config:
            key_value = keyed_values_config[value_key]
            if value_type == "header":
                # HTTP header fields are case-insensitive
                # https://www.w3.org/Protocols/rfc2616/rfc2616-sec4.html#sec4.2
                key_value = key_value.lower()
            if match_function == "exists":
                inp = {value_key: key_value}
                if ignore_case:
                    inp["ignore_case"] = ignore_case
                expr_list.append(
                    create_match_tree_expr(value_type, match_function, inp)
                )
            else:
                for value in keyed_values_config[match_function]:
                    inp = {value_key: key_value, "value": value}
                    if ignore_case:
                        inp["ignore_case"] = ignore_case
                    expr_list.append(
                        create_match_tree_expr(value_type, match_function, inp)
                    )
    for key in keyed_values_config.keys():
        if key not in match_functions and key not in options:
            raise ORMInternalParserException("ERROR: unhandled key : " + key)
    tree = {"or": expr_list}
    return {"not": tree} if is_negation(keyed_values_config) else tree


def parse_match_binary_operator(operator, expressions):
    op = None
    if operator == "any":
        op = "or"
    elif operator == "all":
        op = "and"
    else:
        raise ORMInternalParserException(
            "ERROR: unhandled binary operator: " + operator
        )
    expr_list = []
    for expr in expressions:
        if "all" in expr:
            expr_list.append(parse_match_binary_operator("all", expr["all"]))
        elif "any" in expr:
            expr_list.append(parse_match_binary_operator("any", expr["any"]))
        elif "paths" in expr:
            expr_list.append(parse_match_values(expr["paths"], "path"))
        elif "query" in expr:
            expr_list.append(parse_match_values(expr["query"], "query"))
        elif "method" in expr:
            expr_list.append(parse_match_values(expr["method"], "method"))
        elif "header" in expr:
            expr_list.append(
                parse_match_keyed_values(expr["header"], "header", "field")
            )
        else:
            raise ORMInternalParserException(
                "ERROR: unhandled key in: " + str(expr.keys())
            )
    return {op: expr_list}


def minify_match_tree(match_tree):
    op = None
    if "and" in match_tree:
        op = "and"
    elif "or" in match_tree:
        op = "or"
    else:
        return match_tree
    mini_tree = None
    if len(match_tree[op]) == 1:
        mini_tree = minify_match_tree(match_tree[op][0])
    else:
        mini_tree = {op: []}
        for branch in match_tree[op]:
            mini_tree[op].append(minify_match_tree(branch))
    return mini_tree


def create_match_tree(matches, domain=None):
    expr_list = []
    if domain:
        matches = {"all": [
            {"header": {
                "field": "host",
                "exact": [domain]
            }},
            matches
        ]}
    for key, value in matches.items():
        if key in ["all", "any"]:
            expr_list.append(parse_match_binary_operator(key, value))
        else:
            raise ORMInternalParserException("ERROR: unhandled key in: " + key)
    return {"and": expr_list}


def get_match_tree(matches, domain=None):
    return minify_match_tree(create_match_tree(matches, domain=domain))


def default_handle_condition_list(data_list_in, op, negate):
    # pylint:disable=unused-argument
    return data_list_in


def default_handle_match(src, fun, inp, negate):
    # pylint:disable=unused-argument
    return None


def traverse_match(func, match, negate=False):
    src = match["source"]
    fun = match["function"]
    inp = match["input"]
    return func["handle_match"](src, fun, inp, negate)


def traverse_condition_list(func, match_tree_list, operator, negate=False):
    data_list_in = []
    for match_tree in match_tree_list:
        data_in = traverse_match_tree(func, match_tree)
        data_list_in.append(data_in)
    return func["handle_condition_list"](data_list_in, operator, negate)


def traverse_match_tree(func, match_tree, negate=False):
    """
    Performs a depth first traversal of a match_tree.
    Supply the following functions to be invoked during traversal:

    handle_condition_list(data_list_in, op, negate)
      data_list_in: list of data returned from handle_match
      op:           string, 'and' or 'or'
      negate:       boolean

    handle_match(src, fun, inp, negate)
      src:     string, match source
      fun:     string, match function type
      inp:     anything, match input
      negate:  boolean
    """

    if not "handle_condition_list" in func:
        func["handle_condition_list"] = default_handle_condition_list
    if not "handle_match" in func:
        func["handle_match"] = default_handle_match
    if "and" in match_tree:
        data_out = traverse_condition_list(
            func, match_tree["and"], "and", negate=negate
        )
    elif "or" in match_tree:
        data_out = traverse_condition_list(func, match_tree["or"], "or", negate=negate)
    elif "match" in match_tree:
        data_out = traverse_match(func, match_tree["match"], negate=negate)
    elif "not" in match_tree:
        data_out = traverse_match_tree(func, match_tree["not"], negate=(not negate))
    else:
        raise ORMInternalParserException(
            "ERROR: unhandled condition operator: " + str(match_tree.keys)
        )
    return data_out


def parse_document(doc):
    """
    Returns dict with 'rules' and 'tests' from a single yaml document,
    where 'rules' is a domain keyed dict containing lists of ORM rules.
    """

    if doc.get("schema_version", 2) == 2:
        return parse_document_v2(doc)
    else:
        raise ORMInternalParserException(
            "schema_version %s not supported" % doc["schema_version"]
        )


def parse_document_v2(doc):
    """ The document parser for schema version 2 """

    if doc.get("schema_version"):
        del doc["schema_version"]
    parsed_doc = {"rules": {}, "tests": doc.get("tests", [])}
    for rule in doc.get("rules"):
        domains = rule.get("domains", [])
        if not domains:
            parsed_doc["rules"].setdefault("", [])
            parsed_doc["rules"][""].append(rule)
        else:
            for domain in domains:
                parsed_doc["rules"].setdefault(domain, [])
                parsed_doc["rules"][domain].append(rule)
    return parsed_doc


def normalize(string):
    return re.sub(r"(_)\1{1,}", r"\1", re.sub(r"[^a-z0-9]", "_", string)).strip("_")


def normalize_lower(string):
    return normalize(string.lower())


def get_unique_id(name_counter, raw_name):
    name = normalize_lower(raw_name)
    if name in name_counter:
        name_counter[name] += 1
        return name + "_" + str(name_counter[name])
    name_counter[name] = 1
    return name


def set_rule_defaults(rule, defaults):
    rule.setdefault("actions", {})
    if defaults.get("https_redirection", False):
        if "redirect" not in rule["actions"]:
            rule["actions"]["https_redirection"] = rule["actions"].get(
                "https_redirection", True
            )


def parse_rules(yml_files, defaults=None):
    """
    Returns dict with 'rules' and 'tests' merged from a list of yaml files,
    where 'rules' is a domain keyed dict containing lists of ORM rules.
    """
    name_counter = {}
    merged_documents = {"rules": {}, "tests": []}
    for yml_file in yml_files:
        yml_docs = parse_yaml_file(yml_file)
        for doc in yml_docs:
            parsed_doc = parse_document(doc)
            for domain, rules in sorted(parsed_doc["rules"].items()):
                merged_documents["rules"].setdefault(domain, [])
                for rule in rules:
                    rule_copy = copy.deepcopy(rule)
                    rule_copy["_rule_id"] = get_unique_id(
                        name_counter, rule["description"]
                    )
                    rule_copy["_orm_source_file"] = yml_file
                    if defaults:
                        set_rule_defaults(rule_copy, defaults)
                    merged_documents["rules"][domain].append(rule_copy)
            for test in parsed_doc["tests"]:
                test_copy = copy.deepcopy(test)
                test_copy["_orm_source_file"] = yml_file
                merged_documents["tests"].append(test_copy)
    return merged_documents


def parse_globals(yml_file):
    globals_docs = parse_yaml_file(yml_file)
    if len(globals_docs) != 1:
        raise ORMParserException("There must be exactly one globals document")
    doc = globals_docs[0]
    return doc

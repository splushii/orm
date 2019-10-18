import sys
import re
from urllib.parse import urlparse
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def python_regex(orm_regex):
    return "^{}$".format(orm_regex)


def run_tests(tests, target, verify_certs=True):
    # pylint:disable=too-many-locals,too-many-branches,too-many-statements
    for test in tests:
        print("Run tests from: {}".format(test["_orm_source_file"]))
        name = test.get("name")
        req_method = test["request"].get("method", "GET")
        url = test["request"]["url"]
        headers = test["request"].get("headers", [])
        expect_status = test["expect"].get("status")
        expect_body = test["expect"].get("body", [])
        expect_headers = test["expect"].get("headers", [])
        print("Test: {}".format(name))

        url_parsed = urlparse(url)
        req_url = "{scheme}://{netloc}{path}".format(
            scheme=url_parsed.scheme, netloc=target, path=url_parsed.path
        )

        if url_parsed.query:
            req_url = "{}?{}".format(req_url, url_parsed.query)
        if url_parsed.fragment:
            req_url = "{}#{}".format(req_url, url_parsed.fragment)

        req_headers = {"Host": url_parsed.netloc}
        for header in headers:
            req_headers[header["field"]] = header["value"]

        print("request {}: {}".format(req_method, req_url))
        r = requests.request(
            req_method,
            req_url,
            headers=req_headers,
            verify=verify_certs,
            allow_redirects=False,
        )

        if expect_status:
            if r.status_code != expect_status:
                print(
                    "Got status code {}, expect {}".format(r.status_code, expect_status)
                )
                sys.exit(1)

        for b in expect_body:
            regex = python_regex(b["regex"])
            if not re.search(regex, r.text, flags=re.MULTILINE):
                print("Body did not match {}".format(regex))
                print("Body:\n{}".format(r.text))
                sys.exit(1)

        for h in expect_headers:
            # Make sure that all expected headers are there
            if h["field"] not in r.headers:
                print("Header {} not found".format(h["field"]))
                sys.exit(1)

            # Check that the expected header contains the correct data
            for header in expect_headers:
                hf = header["field"]
                hr = python_regex(header["regex"])
                if not re.search(hr, r.headers.get(hf)):
                    print(
                        "Header {} contains {}, expected {}".format(
                            hf, r.headers.get(hf), hr
                        )
                    )
                    sys.exit(1)

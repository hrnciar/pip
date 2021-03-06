"""
Tests for the resolver
"""

import os
import re

import pytest
import yaml

from tests.lib import DATA_DIR, create_basic_wheel_for_package, path_to_url

_conflict_finder_pat = re.compile(
    # Conflicting Requirements: \
    # A 1.0.0 requires B == 2.0.0, C 1.0.0 requires B == 1.0.0.
    r"""
        (?P<package>[\w\-_]+?)
        [ ]
        (?P<version>\S+?)
        [ ]requires[ ]
        (?P<selector>.+?)
        (?=,|\.$)
    """,
    re.X
)


def generate_yaml_tests(directory):
    """
    Generate yaml test cases from the yaml files in the given directory
    """
    for yml_file in directory.glob("*/*.yml"):
        data = yaml.safe_load(yml_file.read_text())
        assert "cases" in data, "A fixture needs cases to be used in testing"

        # Strip the parts of the directory to only get a name without
        # extension and resolver directory
        base_name = str(yml_file)[len(str(directory)) + 1:-4]

        base = data.get("base", {})
        cases = data["cases"]

        for i, case_template in enumerate(cases):
            case = base.copy()
            case.update(case_template)

            case[":name:"] = base_name
            if len(cases) > 1:
                case[":name:"] += "-" + str(i)

            if case.pop("skip", False):
                case = pytest.param(case, marks=pytest.mark.xfail)

            yield case


def id_func(param):
    """
    Give a nice parameter name to the generated function parameters
    """
    if isinstance(param, dict) and ":name:" in param:
        return param[":name:"]

    retval = str(param)
    if len(retval) > 25:
        retval = retval[:20] + "..." + retval[-2:]
    return retval


def convert_to_dict(string):

    def stripping_split(my_str, splitwith, count=None):
        if count is None:
            return [x.strip() for x in my_str.strip().split(splitwith)]
        else:
            return [x.strip() for x in my_str.strip().split(splitwith, count)]

    parts = stripping_split(string, ";")

    retval = {}
    retval["depends"] = []
    retval["extras"] = {}

    retval["name"], retval["version"] = stripping_split(parts[0], " ")

    for part in parts[1:]:
        verb, args_str = stripping_split(part, " ", 1)
        assert verb in ["depends"], "Unknown verb {!r}".format(verb)

        retval[verb] = stripping_split(args_str, ",")

    return retval


def handle_install_request(script, requirement):
    assert isinstance(requirement, str), (
        "Need install requirement to be a string only"
    )
    result = script.pip(
        "install",
        "--no-index", "--find-links", path_to_url(script.scratch_path),
        requirement, "--verbose",
        allow_stderr_error=True,
        allow_stderr_warning=True,
    )

    retval = {
        "_result_object": result,
    }
    if result.returncode == 0:
        # Check which packages got installed
        retval["install"] = []

        for path in result.files_created:
            if path.endswith(".dist-info"):
                name, version = (
                    os.path.basename(path)[:-len(".dist-info")]
                ).rsplit("-", 1)

                # TODO: information about extras.

                retval["install"].append(" ".join((name, version)))

        retval["install"].sort()

        # TODO: Support checking uninstallations
        # retval["uninstall"] = []

    elif "conflicting" in result.stderr.lower():
        retval["conflicting"] = []

        message = result.stderr.rsplit("\n", 1)[-1]

        # XXX: There might be a better way than parsing the message
        for match in re.finditer(message, _conflict_finder_pat):
            di = match.groupdict()
            retval["conflicting"].append(
                {
                    "required_by": "{} {}".format(di["name"], di["version"]),
                    "selector": di["selector"]
                }
            )

    return retval


@pytest.mark.yaml
@pytest.mark.parametrize(
    "case", generate_yaml_tests(DATA_DIR.parent / "yaml"), ids=id_func
)
def test_yaml_based(script, case):
    available = case.get("available", [])
    requests = case.get("request", [])
    transaction = case.get("transaction", [])

    assert len(requests) == len(transaction), (
        "Expected requests and transaction counts to be same"
    )

    # Create a custom index of all the packages that are supposed to be
    # available
    # XXX: This doesn't work because this isn't making an index of files.
    for package in available:
        if isinstance(package, str):
            package = convert_to_dict(package)

        assert isinstance(package, dict), "Needs to be a dictionary"

        create_basic_wheel_for_package(script, **package)

    available_actions = {
        "install": handle_install_request
    }

    # use scratch path for index
    for request, expected in zip(requests, transaction):
        # The name of the key is what action has to be taken
        assert len(request.keys()) == 1, "Expected only one action"

        # Get the only key
        action = list(request.keys())[0]

        assert action in available_actions.keys(), (
            "Unsupported action {!r}".format(action)
        )

        # Perform the requested action
        effect = available_actions[action](script, request[action])

        result = effect["_result_object"]
        del effect["_result_object"]

        assert effect == expected, str(result)

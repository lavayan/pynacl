# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import absolute_import, division, print_function

import getpass
import io
import os
import time

from clint.textui.progress import Bar as ProgressBar

import invoke

import requests


JENKINS_URL = "https://jenkins.cryptography.io/job/pynacl-wheel-builder"


def wait_for_build_completed(session):
    # Wait 20 seconds before actually checking if the build is complete, to
    # ensure that it had time to really start.
    time.sleep(20)
    while True:
        response = session.get(
            "{0}/lastBuild/api/json/".format(JENKINS_URL),
            headers={
                "Accept": "application/json",
            }
        )
        response.raise_for_status()
        if not response.json()["building"]:
            assert response.json()["result"] == "SUCCESS"
            break
        time.sleep(1.0)


def download_artifacts(session):
    response = session.get(
        "{0}/lastBuild/api/json/".format(JENKINS_URL),
        headers={
            "Accept": "application/json"
        }
    )
    response.raise_for_status()
    assert not response.json()["building"]
    assert response.json()["result"] == "SUCCESS"

    paths = []

    last_build_number = response.json()["number"]
    for run in response.json()["runs"]:
        if run["number"] != last_build_number:
            print(
                "Skipping {0} as it is not from the latest build ({1})".format(
                    run["url"], last_build_number
                )
            )
            continue

        response = session.get(
            run["url"] + "api/json/",
            headers={
                "Accept": "application/json",
            }
        )
        response.raise_for_status()
        for artifact in response.json()["artifacts"]:
            response = session.get(
                "{0}artifact/{1}".format(run["url"], artifact["relativePath"]),
                stream=True
            )
            assert response.headers["content-length"]
            print("Downloading {0}".format(artifact["fileName"]))
            bar = ProgressBar(
                expected_size=int(response.headers["content-length"]),
                filled_char="="
            )
            content = io.BytesIO()
            for data in response.iter_content(chunk_size=8192):
                content.write(data)
                bar.show(content.tell())
            assert bar.expected_size == content.tell()
            bar.done()
            out_path = os.path.join(
                os.path.dirname(__file__),
                "dist",
                artifact["fileName"],
            )
            with open(out_path, "wb") as f:
                f.write(content.getvalue())
            paths.append(out_path)
    return paths


@invoke.task
def release(ctx, version):
    """
    ``version`` should be a string like '0.4' or '1.0'.
    """
    invoke.run("git tag -s {0} -m '{0} release'".format(version))
    invoke.run("git push --tags")

    invoke.run("python setup.py sdist")

    invoke.run("twine upload -s dist/PyNaCl-{0}* ".format(version))

    session = requests.Session()

    # This tells the CDN to delete the cached response for the URL. We do this
    # so that the Jenkins builders will see the new sdist immediately when they
    # go to build the wheels.
    response = session.request(
        "PURGE", "https://pypi.python.org/simple/pynacl/"
    )
    response.raise_for_status()

    token = getpass.getpass("Input the Jenkins token: ")
    response = session.post(
        "{0}/build".format(JENKINS_URL),
        params={
            "cause": "Building wheels for {0}".format(version),
            "token": token
        }
    )
    response.raise_for_status()
    wait_for_build_completed(session)
    paths = download_artifacts(session)
    invoke.run("twine upload {0}".format(" ".join(paths)))

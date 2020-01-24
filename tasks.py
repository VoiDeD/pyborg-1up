from pathlib import Path
from pprint import pprint

import attr
from fabric2 import Connection
from invoke import task


@task
def deploy(c, git=True, restart=False, sync=False, target_machine="trotsky"):
    "push code to a server (configure your own here)"

    # desired_services = ["pyborg_discord", "pyborg_http", "pyborg_twitter", "pyborg_mastodon"]
    desired_services = ["pyborg_discord", "pyborg_http"]
    if git:
        c.run("git push --all")
    conn = Connection(target_machine)
    with conn.cd("src/pyborg-1up"):
        conn.run("git fetch")
        conn.run("git stash")
        conn.run("git pull")
        conn.run("git stash pop")
        if sync:
            conn.run("~/.poetry/bin/poetry install -v")  # poetry manages this application
        if restart:
            units = " ".join(desired_services)
            conn.run("sudo systemctl restart {}".format(units), pty=True)
            print("Restarted services.")
        print("Deploy Completed.")


@task
def release(c, clean=True, docker=False):
    "cut a release of pyborg"

    with c.cd("pyborg"):
        if clean:
            pass
        c.run("poetry run python --version", echo=True)
        c.run("poetry build")

    if docker:
        # Build and push jrabbit/pyborg[:3], needs working d-c and docker
        c.run("docker-compose build")
        c.run("docker-compose push")


@task
def bandit(c):
    "security audit tool"
    c.run("poetry run bandit --exclude=build,test -s B311 -r pyborg", pty=True)

@task
def docs(c):
    "run autobuilder for local docs generation"
    src = Path("docs", "source")
    docs = Path("docs", "build")
    c.run(f"poetry run sphinx-autobuild {src} {docs}", pty=True)

@task
def test(c):
    "this runs tox"
    c.run("poetry run tox -r")


@task
def docker_gauntlet(c):
    "there's so many ways to fuck this up on install let's try them all!"
    versions_list = ["3.6", "3.7", "3"]
    for py_version in versions_list:
        c.run(f"docker pull python:{py_version}")
    @attr.s
    class Strat():
        name = attr.ib()
        cmd = attr.ib()
        wheel = attr.ib(default=None)
    import pyborg
    whl_version = pyborg.__version__
    strats = [Strat(name="pip_wheel", cmd="pip install /srv/src/pyborg_1up/pyborg/dist/{wheel} && pyborg version", wheel="pyborg-{}-py2.py3-none-any.whl".format(whl_version)),
              Strat(name="pip_install_src", cmd="pip install /srv/src/pyborg1_up/pyborg && pyborg version"),
              Strat(name="pipenv", cmd="pip install pipenv && cd /srv/src/pyborg1_up/ && pipenv sync && pipenv run pyborg version")
             ]
    results = {}
    for py_version in versions_list:
        # docker scripting to install via pip, pipenv and setuptools (the first two work normally, but test build arefacts)
        for strat in strats:
            print(py_version, strat.name)
            if strat.wheel:
                lcmd = strat.cmd.format(wheel=strat.wheel)
            else:
                lcmd = strat.cmd
            ret = c.run("docker run -v $PWD:/srv/src/pyborg1_up -v $PWD/misc/docker_caches:/root/.cache --rm -it python:{} bash -c '{cmd}'".format(py_version, cmd=lcmd), pty=True, warn=True)
            results[f"{py_version}_{strat.name}"] = ret.ok
    pprint(results)

@task
def systemd_tests(c):
    "test systemd units with a podman container"
    with c.cd("misc/systemd_explorer"):
        c.run("podman build -t pyborg_systemd_explorer .", pty=True)
    g = c.run("podman run -d -v $PWD:/srv pyborg_systemd_explorer")
    cid = g.stdout
    c.run(f"podman exec -ti {cid} /srv/misc/systemd_explorer/systemd_install.sh", pty=True)

@task
def outdated(c):
    "outdated packages"
    c.run("poetry show -o")


@task
def lint(c, mypy=True, pylint=False):
    "style & type checks"
    pp = Path("pyborg", "pyborg")
    if mypy:
        print("mypy")
        c.run(f"poetry run mypy {pp}", warn=True)
    if pylint:
        print("pylint")
        entrypoint = Path("pyborg", "pyborg_entrypoint.py")
        c.run(f"poetry run pylint {pp} {entrypoint}", warn=True)
    print("flake8")
    c.run("poetry run flake8 --config=tox.ini --count pyborg", warn=True)

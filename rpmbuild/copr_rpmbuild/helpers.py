import logging
import munch
import subprocess
import rpm
import glob
import os
import sys
import re
import configparser
import datetime
import pipes
from threading import Timer

from six.moves.urllib.parse import urlparse

log = logging.getLogger("__main__")

CONF_DIRS = [os.getcwd(), "/etc/copr-rpmbuild"]


class SourceType:
    LINK = 1
    UPLOAD = 2
    PYPI = 5
    RUBYGEMS = 6
    SCM = 8
    CUSTOM = 9


def cmd_debug(result):
    log.debug("")
    log.debug("cmd: {0}".format(result.cmd))
    log.debug("cwd: {0}".format(result.cwd))
    log.debug("rc: {0}".format(result.returncode))
    log.debug("stdout: {0}".format(result.stdout))
    log.debug("stderr: {0}".format(result.stderr))
    log.debug("")


def cmd_readable(cmd):
    return ' '.join([pipes.quote(part) for part in cmd])


def run_cmd(cmd, cwd=".", preexec_fn=None):
    """
    Runs given command in a subprocess.

    :param list(str) cmd: command to be executed and its arguments
    :param str cwd: In which directory to execute the command
    :param func preexec_fn: a callback invoked before exec in subprocess

    :raises RuntimeError
    :returns munch.Munch(cmd, stdout, stderr, returncode)
    """
    log.info('Running: ' + cmd_readable(cmd))

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, preexec_fn=preexec_fn)
        (stdout, stderr) = process.communicate()
    except FileNotFoundError:
        raise RuntimeError("Package with `{}` command is not installed".format(cmd[0]))
    except OSError as e:
        raise RuntimeError(str(e))

    result = munch.Munch(
        cmd=cmd,
        stdout=stdout.decode('utf-8').strip(),
        stderr=stderr.decode('utf-8').strip(),
        returncode=process.returncode,
        cwd=cwd
    )
    cmd_debug(result)

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return result


def locate_spec(dirpath):
    spec_path = None
    path_matches = glob.glob(os.path.join(dirpath, '*.spec'))
    for path_match in path_matches:
        if os.path.isfile(path_match):
            spec_path = path_match
            break
    if not spec_path:
        raise RuntimeError('No .spec found at {0}'.format(dirpath))
    return spec_path


def locate_srpm(dirpath):
    srpm_path = None
    path_matches = glob.glob(os.path.join(dirpath, '*.src.rpm'))
    for path_match in path_matches:
        if os.path.isfile(path_match):
            srpm_path = path_match
            break
    if not srpm_path:
        raise RuntimeError('No .src.rpm found at {0}'.format(dirpath))
    return srpm_path


def get_package_name(spec_path):
    """
    Obtain name of a package described by spec
    at spec_path.

    :param str spec_path: path to a spec file

    :returns str: package name

    :raises PackageNameCouldNotBeObtainedException
    """
    ts = rpm.ts()

    try:
        rpm_spec = ts.parseSpec(spec_path)
    except ValueError as e:
        log.debug("Could not parse {0} with error {1}. Trying manual parsing."
                 .format(spec_path, str(e)))

        with open(spec_path, 'r') as spec_file:
            spec_lines = spec_file.readlines()

        patterns = [
            re.compile(r'^(name):\s*(\S*)$', re.IGNORECASE),
            re.compile(r'^%global\s*(\S*)\s*(\S*)$'),
            re.compile(r'^%define\s*(\S*)\s*(\S*)$')]

        for spec_line in spec_lines:
            for pattern in patterns:
                match = pattern.match(spec_line)
                if not match:
                    continue
                rpm.addMacro(
                    match.group(1), match.group(2))

    package_name = rpm.expandMacro("%{name}")
    rpm.reloadConfig()

    if not re.match(r'[a-zA-Z0-9-._+]+', package_name):
        raise PackageNameCouldNotBeObtainedException(
            "Got invalid package package name '{0}' from {1}.".format(package_name, spec_path))

    return package_name


def string2list(string):
    return [elem.strip() for elem in re.split(r"\s*,\s*|\s+", string) if elem]


def read_config(config_path=None):
    config = configparser.RawConfigParser(defaults={
        "resultdir": "/var/lib/copr-rpmbuild/results",
        "lockfile": "/var/lib/copr-rpmbuild/lockfile",
        "logfile": "/var/lib/copr-rpmbuild/main.log",
        "pidfile": "/var/lib/copr-rpmbuild/pid",
        "enabled_source_protocols": "https ftps",
    })
    config_paths = [os.path.join(path, "main.ini") for path in CONF_DIRS]
    config.read(config_path or reversed(config_paths))
    if not config.sections():
        log.error("No configuration file main.ini in: {0}".format(" ".join(CONF_DIRS)))
        sys.exit(1)
    return config


def path_join(*args):
    return os.path.normpath('/'.join(args))


def get_mock_uniqueext():
    """
    This is a hack/workaround not to reuse already setup
    chroot from a previous run but to always setup a new
    one. Upon key interrupt during build, mock chroot
    becomes further unuseable and there are also problems
    with method _fixup_build_user in mock for make_srpm
    method together with --private-users=pick for sytemd-
    nspawn.
    """
    return datetime.datetime.now().strftime('%s.%f')


def extract_srpm(srpm_path, destination):
    """
    Extracts srpm content to the target directory.

    raises: CheckOutputError
    """
    cwd = os.getcwd()
    os.chdir(destination)
    log.debug('Extracting srpm {0} to {1}'.format(srpm_path, destination))
    try:
        cmd = "rpm2cpio {path} | cpio -idmv".format(path=pipes.quote(srpm_path))
        subprocess.check_call(cmd, shell=True)
    finally:
        os.chdir(cwd)


def build_srpm(srcdir, destdir):
    cmd = [
        'rpmbuild', '-bs',
        '--define', '_sourcedir ' + srcdir,
        '--define', '_rpmdir '    + srcdir,
        '--define', '_builddir '  + srcdir,
        '--define', '_specdir '   + srcdir,
        '--define', '_srcrpmdir ' + destdir,
    ]

    specfiles = glob.glob(os.path.join(srcdir, '*.spec'))
    if len(specfiles) == 0:
        raise RuntimeError("no spec file available")

    if len(specfiles) > 1:
        raise RuntimeError("too many specfiles: {0}".format(
            ', '.join(specfiles)
        ))

    cmd += [specfiles[0]]
    run_cmd(cmd)


def copr_chroot_to_task_id(copr, chroot):
    copr_token = re.sub('@', 'group_', copr)
    copr_token = re.sub('/', '-', copr_token)
    return copr_token +'-'+chroot


def parse_copr_name(name):
    m = re.match(r"([^/]+)/(.*)", name)
    ownername = m.group(1)
    projectname = m.group(2)
    return ownername, projectname


def dump_live_log(logfile):
    filter_continuing_lines = r"sed 's/.*\x0D\([^\x0a]\)/\1/g' --unbuffered"
    tee_output = "tee -a {0}".format(pipes.quote(logfile))
    cmd = filter_continuing_lines + "|" + tee_output
    tee = subprocess.Popen(cmd, stdin=subprocess.PIPE, shell=True)
    os.dup2(tee.stdin.fileno(), sys.stdout.fileno())
    os.dup2(tee.stdin.fileno(), sys.stderr.fileno())


class GentlyTimeoutedPopen(subprocess.Popen):
    timers = []

    def __init__(self, cmd, timeout=None, **kwargs):
        log.info('Running (timeout={to}): {cmd}'.format(
            to=str(timeout),
            cmd=cmd_readable(cmd),
        ))

        super(GentlyTimeoutedPopen, self).__init__(cmd, **kwargs)
        if not timeout:
            return

        def timeout_cb(me, string, signal):
            log.error(" !! Copr timeout => sending {0}".format(string))
            me.send_signal(signal)

        delay = timeout
        for string, signal in [('INT', 2), ('TERM', 15), ('KILL', 9)]:
            timer = Timer(delay, timeout_cb, [self, string, signal])
            timer.start()
            self.timers.append(timer)
            delay = delay + 10

    def done(self):
        for timer in self.timers:
            timer.cancel()

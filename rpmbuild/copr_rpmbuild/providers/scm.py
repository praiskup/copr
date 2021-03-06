import os
import re
import logging
import re

from copr_rpmbuild import helpers

from jinja2 import Environment, FileSystemLoader
from ..helpers import run_cmd, CONF_DIRS, get_mock_uniqueext
from .base import Provider

from six.moves.urllib.parse import urlparse


log = logging.getLogger("__main__")


class ScmProvider(Provider):
    def __init__(self, source_json, outdir, config):
        super(ScmProvider, self).__init__(source_json, outdir, config)
        self.scm_type = source_json.get('type') or 'git'
        self.clone_url = source_json.get('clone_url')
        self.committish = source_json.get('committish') or 'master'
        self.repo_subdir = source_json.get('subdirectory') or ''
        self.spec_relpath = source_json.get('spec') or ''
        self.srpm_build_method = source_json.get('srpm_build_method') or 'rpkg'
        self.repo_dirname = os.path.splitext(os.path.basename(
            self.clone_url.rstrip('/')))[0]
        self.repo_path = helpers.path_join(self.workdir, self.repo_dirname)
        self.repo_subpath = helpers.path_join(self.repo_path, self.repo_subdir)
        self.spec_path = helpers.path_join(
            self.repo_path, os.path.join(self.repo_subdir, self.spec_relpath))

    def generate_rpkg_config(self):
        parsed_clone_url = urlparse(self.clone_url)
        distgit_config_section = None

        index = 0
        config_section = 'distgit{index}'.format(index=index)
        while self.config.has_section(config_section):
            distgit_hostname_pattern = self.config.get(
                config_section, 'distgit_hostname_pattern')
            if re.match(distgit_hostname_pattern, parsed_clone_url.netloc):
                distgit_config_section = config_section
                break
            index += 1
            config_section = 'distgit{index}'.format(index=index)

        if not distgit_config_section:
            distgit_config_section = 'main'

        distgit_lookaside_url = self.config.get(
            distgit_config_section, 'distgit_lookaside_url', fallback='').strip('/').format(
                scheme=parsed_clone_url.scheme, netloc=parsed_clone_url.netloc)

        distgit_clone_url = self.config.get(
            distgit_config_section, 'distgit_clone_url', fallback='').strip('/').format(
                scheme=parsed_clone_url.scheme, netloc=parsed_clone_url.netloc)

        jinja_env = Environment(loader=FileSystemLoader(CONF_DIRS))
        template = jinja_env.get_template("rpkg.conf.j2")
        config = template.render(lookaside_url=distgit_lookaside_url,
                                 clone_url=distgit_clone_url)

        log.debug('Generated rpkg config:\n'+config+'\n')
        config_dir_path = os.path.join(os.getenv('HOME'), '.config')

        try:
            os.makedirs(config_dir_path)
        except OSError:
            pass

        config_path = os.path.join(config_dir_path, 'rpkg.conf')
        log.debug('Writing config into '+config_path)

        f = open(config_path, "w+")
        f.write(config)
        f.close()

        return config_path

    def get_rpkg_command(self):
        self.generate_rpkg_config()
        return ['rpkg', 'srpm', '--outdir', self.outdir, '--spec', self.spec_path]

    def get_tito_command(self):
        return ['tito', 'build', '--srpm', '--output', self.outdir]

    def get_tito_test_command(self):
        return ['tito', 'build', '--test', '--srpm', '--output', self.outdir]

    def get_make_srpm_command(self):
        mock_workdir = '/mnt' + self.workdir
        mock_outdir = '/mnt' + self.outdir
        mock_repodir = helpers.path_join(mock_workdir, self.repo_dirname)
        mock_cwd = helpers.path_join(mock_repodir, self.repo_subdir)
        mock_spec_path = helpers.path_join(
            mock_repodir, os.path.join(self.repo_subdir, self.spec_relpath))

        mock_bind_mount_cmd_part = \
            '--plugin-option=bind_mount:dirs=(("{0}", "{1}"), ("{2}", "{3}"))'\
            .format(self.workdir, mock_workdir, self.outdir, mock_outdir)

        makefile_path = os.path.join(mock_repodir, '.copr', 'Makefile')
        make_srpm_cmd_part = \
            'cd {0}; make -f {1} srpm outdir="{2}" spec="{3}"'\
            .format(mock_cwd, makefile_path, mock_outdir, mock_spec_path)

        return ['mock', '--uniqueext', get_mock_uniqueext(),
                '-r', '/etc/copr-rpmbuild/make_srpm_mock.cfg',
                mock_bind_mount_cmd_part, '--chroot', make_srpm_cmd_part]

    def produce_srpm(self):
        self.clone_and_checkout()
        cmd = {
            'rpkg': self.get_rpkg_command,
            'tito': self.get_tito_command,
            'tito_test': self.get_tito_test_command,
            'make_srpm': self.get_make_srpm_command,
        }[self.srpm_build_method]()
        return run_cmd(cmd, cwd=self.repo_subpath)

    def produce_sources(self):
        self.clone_and_checkout()

        copy_cmd = ['cp', '-r', '.', self.outdir]
        run_cmd(copy_cmd, cwd=self.repo_subpath)

        cmd = ['rpkg', '-C', self.generate_rpkg_config(),
               'sources', '--outdir', self.outdir]
        return run_cmd(cmd, cwd=self.repo_subpath)

    def clone_and_checkout(self):
        if self.scm_type == 'git':
            clone_cmd = ['git', 'clone', self.clone_url,
                         self.repo_path, '--depth', '500',
                         '--no-single-branch']
        else:
            clone_cmd = ['git', 'svn', 'clone', self.clone_url,
                         self.repo_path]

        try:
            helpers.run_cmd(clone_cmd)
        except RuntimeError as e:
            log.error(str(e))
            if self.scm_type == 'git':
                helpers.run_cmd(['git', 'clone', self.clone_url, self.repo_path])
            else:
                raise e

        checkout_cmd = ['git', 'checkout', self.committish]
        helpers.run_cmd(checkout_cmd, cwd=self.repo_path)

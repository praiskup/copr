%if 0%{?fedora} || 0%{?rhel} > 7
%global __python        %__python3
%global python          python3
%global python_pfx      python3
%global rpm_python      python3-rpm
%else
%global __python        %__python2
%global python          python2
%global python_pfx      python
%global rpm_python      rpm-python
%endif

# do not build debuginfo sub-packages
%define debug_package %nil

%define latest_requires() \
Requires: %1 \
%{expand: %%global latest_requires_packages %1 %%{?latest_requires_packages}}

Name:    copr-rpmbuild
Version: 0.33
Summary: Run COPR build tasks
Release: 1%{?dist}
URL: https://pagure.io/copr/copr
License: GPLv2+

# Source is created by:
# git clone %%url && cd copr
# tito build --tgz --tag %%name-%%version-%%release
Source0:    %name-%version.tar.gz

BuildRequires: %{python}-devel
BuildRequires: %{python}-distro
%if 0%{?rhel} == 0 || 0%{?rhel} != 6
BuildRequires: %{python}-httmock
%endif
BuildRequires: %{rpm_python}
BuildRequires: asciidoc
BuildRequires: %{python}-setuptools
BuildRequires: %{python}-pytest
BuildRequires: %{python_pfx}-munch
BuildRequires: %{python}-requests
BuildRequires: %{python_pfx}-jinja2

BuildRequires: python-rpm-macros

%if %{?python} == "python2"
BuildRequires: python2-configparser
BuildRequires: python2-mock
Requires: python2-configparser
%endif

Requires: %python
Requires: %{python_pfx}-jinja2
Requires: %{python_pfx}-munch
Requires: %{python}-requests
Requires: %{python_pfx}-simplejson

Requires: mock
Requires: git
Requires: git-svn
# for the /bin/unbuffer binary
Requires: expect
Requires: qemu-user-static

%if 0%{?fedora} || 0%{?rhel} > 7
Recommends: rpkg
Recommends: python-srpm-macros
Suggests: tito
Suggests: rubygem-gem2rpm
Suggests: pyp2rpm
%endif

%description
Provides command capable of running COPR build-tasks.
Example: copr-rpmbuild 12345-epel-7-x86_64 will locally
build build-id 12345 for chroot epel-7-x86_64.


%package -n copr-builder
Summary: copr-rpmbuild with all weak dependencies
Requires: %{name} = %{version}-%{release}

%if 0%{?fedora}
# replacement for yum/yum-utils, to be able to work with el* chroots
# bootstrap_container.
Requires: dnf-yum
Requires: dnf-utils
%endif
# selinux toolset to allow running ansible against the builder
%if 0%{?fedora}
Requires: python3-libselinux
Requires: python3-libsemanage
%else
Requires: libselinux-python
Requires: libsemanage-python
%endif
# for mock to allow: config_opts['nosync'] = True
Requires: nosync
%ifarch x86_64
# multilib counterpart to avoid: config_opts['nosync_force'] = True
Requires: nosync(x86-32)
%endif
Requires: openssh-clients
Requires: pyp2rpm
# We need %%pypi_source defined, which is in 3-29+
Requires: python-srpm-macros >= 3-29
Requires: rpkg
Requires: rsync
Requires: rubygem-gem2rpm
Requires: scl-utils-build
Requires: tito
# yum* to allow mock to build against el* chroots without bootstrap_container
%if 0%{?rhel}
Requires: yum
Requires: yum-utils
%endif

# We want those to be always up-2-date
%latest_requires ca-certificates
%latest_requires distribution-gpg-keys
%if 0%{?fedora}
%latest_requires dnf
%latest_requires dnf-plugins-core
%latest_requires libdnf
%latest_requires librepo
%endif
%latest_requires mock
%latest_requires mock-core-configs
%latest_requires rpm


%description -n copr-builder
Provides command capable of running COPR build-tasks.
Example: copr-rpmbuild 12345-epel-7-x86_64 will locally
build build-id 12345 for chroot epel-7-x86_64.

This package contains all optional modules for building SRPM.


%prep
%setup -q


%check
PYTHON=%{python} ./run_tests.sh


%build
name="%{name}" version="%{version}" summary="%{summary}" %py_build
a2x -d manpage -f manpage man/copr-rpmbuild.1.asciidoc

cat > copr-update-builder <<EOF
#! /bin/sh

# Update the Copr builder machine, can be called anytime Copr build system
# decides to do so (please keep the output idempotent).

# install the latest versions of those packages
dnf update -y %latest_requires_packages
EOF


%install
install -d %{buildroot}%{_sysconfdir}/copr-rpmbuild
install -d %{buildroot}%{_sharedstatedir}/copr-rpmbuild
install -d %{buildroot}%{_sharedstatedir}/copr-rpmbuild/results

install -d %{buildroot}%{_bindir}
install -m 755 main.py %{buildroot}%{_bindir}/copr-rpmbuild
sed -i '1 s|#.*|#! /usr/bin/%python|' %{buildroot}%{_bindir}/copr-rpmbuild
install -m 644 main.ini %{buildroot}%{_sysconfdir}/copr-rpmbuild/main.ini
install -m 644 mock.cfg.j2 %{buildroot}%{_sysconfdir}/copr-rpmbuild/mock.cfg.j2
install -m 644 rpkg.conf.j2 %{buildroot}%{_sysconfdir}/copr-rpmbuild/rpkg.conf.j2
install -m 644 make_srpm_mock.cfg %{buildroot}%{_sysconfdir}/copr-rpmbuild/make_srpm_mock.cfg

install -d %{buildroot}%{_mandir}/man1
install -p -m 644 man/copr-rpmbuild.1 %{buildroot}/%{_mandir}/man1/
install -p -m 755 bin/copr-sources-custom %buildroot%_bindir

name="%{name}" version="%{version}" summary="%{summary}" %py_install

install -p -m 755 copr-update-builder %buildroot%_bindir


%files
%{!?_licensedir:%global license %doc}
%license LICENSE

%{expand:%%%{python}_sitelib}/*

%{_bindir}/copr-rpmbuild
%{_bindir}/copr-sources-custom
%{_mandir}/man1/copr-rpmbuild.1*

%dir %attr(0775, root, mock) %{_sharedstatedir}/copr-rpmbuild
%dir %attr(0775, root, mock) %{_sharedstatedir}/copr-rpmbuild/results

%dir %{_sysconfdir}/copr-rpmbuild
%config(noreplace) %{_sysconfdir}/copr-rpmbuild/main.ini
%config(noreplace) %{_sysconfdir}/copr-rpmbuild/mock.cfg.j2
%config(noreplace) %{_sysconfdir}/copr-rpmbuild/rpkg.conf.j2
%config(noreplace) %{_sysconfdir}/copr-rpmbuild/make_srpm_mock.cfg

%files -n copr-builder
%license LICENSE
%_bindir/copr-update-builder


%changelog
* Fri Dec 06 2019 Pavel Raiskup <praiskup@redhat.com> 0.33-1
- rpmbuild: skip_if_unavailable=1 for non-ACR projects

* Wed Dec 04 2019 Pavel Raiskup <praiskup@redhat.com> 0.32-1
- fix custom method for F31's nspawn (--console=pipe is not default)
- buildrequires: add qemu-user-static for building armhfp
- module_hotfixes support
- define %%copr_username again on copr builders
- skip_if_unavailable=False for copr_base

* Wed Jul 31 2019 Pavel Raiskup <praiskup@redhat.com> 0.31-1
- rpmbuild: make sure librepo/libdnf is always up2date

* Mon Jul 29 2019 Pavel Raiskup <praiskup@redhat.com> 0.30-1
- drop SCM parameters from copr-rpmbuild
- implement --task-file and --task-url parameters (issue#517)

* Fri Jun 07 2019 Pavel Raiskup <praiskup@redhat.com> 0.29-1
- clean /var/cache/mock automatically

* Mon May 27 2019 Pavel Raiskup <praiskup@redhat.com> 0.28-1
- don't use --private-users=pick

* Mon May 20 2019 Pavel Raiskup <praiskup@redhat.com> 0.27-1
- enforce use_host_resolv
- require even nosync.i686

* Tue May 14 2019 Pavel Raiskup <praiskup@redhat.com> 0.26-1
- [rpmbuild] ansible_python_interpreter: /usr/bin/python3
- [rpmbuild] install dnf-utils instead of yum-utils on Fedora
- [rpmbuild] builder: document some of the requires
- [rpmbuild] builder: merge dependencies from playbooks
- [rpmbuild] don't define %%_disable_source_fetch
- [rpmbuild] use six.moves.urllib.parse
- [rpmbuild] download srpm/spec if url contains query string

* Wed Apr 24 2019 Jakub Kadlčík <frostyx@email.cz> 0.25-1
- remove dependency on python3-configparser

* Thu Jan 10 2019 Miroslav Suchý <msuchy@redhat.com> 0.24-1
- create copr-rpmbuild-all subpackage
- Fix `copr-cli mock-config` after switching to APIv3 by preprocessing repos on
frontend
- add python-srpm-macros
- print nice error when suggested package is not installed
- tito and rpkg should be required only by copr-builder
- create copr-builder
- let mock rootdir generation on clients
- rename repos 'url' attribute to 'baseurl'
- provide repo_id in project chroot build config
- Allow per-package chroot-blacklisting by wildcard patterns
- preprocess repo URLs on frontend
- revert back Suggests
- drop "downloading" state
- allow blacklisting packages from chroots

* Fri Oct 19 2018 Miroslav Suchý <msuchy@redhat.com> 0.23-1
- /usr/bin/env python3 -> /usr/bin/python3
- nicer live logs

* Tue Sep 18 2018 clime <clime@redhat.com> 0.22-1
- make spec_template for pypi in build config optional
- EPEL6 fixes
- EPEL7 fixes
- Merge #393 `use git_dir_archive instead of git_dir_pack`
- handle non-existent chroot for given build-id
- fix requests exception
- add support for copr://
- generate some sane mock root param when --copr arg is used
- add --copr arg to build/dump-configs against copr+chroot build defs
- pg#251 Make it possible for user to select pyp2rpm template
- --dump-configs option

* Wed Aug 29 2018 clime <clime@redhat.com> 0.21-1
- [rpmbuild] add possibility to supply rpkg.conf in top-level scm dir
- packaging: Python 2/3, RHEL/Fedora fixes

* Mon Aug 06 2018 clime <clime@redhat.com> 0.20-1
- for py3 use unittest.mock, otherwise mock from python2-mock
- avoid subprocess.communicate(timeout=..)
- BlockingIOError, IOError -> OSError
- hack for optional argparse subparser
- fix shebang for epel7
- use fcntl.lockf (works with python 2.7, too)
- make copr-rpmbuild installable/buildable on el7

* Fri May 18 2018 clime <clime@redhat.com> 0.19-1
- add --with/--without rpmbuild options for build chroot

* Thu Apr 26 2018 Dominik Turecek <dturecek@redhat.com> 0.18-1
- rpkg deployment into COPR - containers + releng continuation
- updates for latest upstream rpkg
- update rpkg.conf.j2 to the latest rpkg version
- s|/bin/env|/usr/bin/env| in shebang

* Fri Feb 23 2018 clime <clime@redhat.com> 0.17-1
- remove unused requires and rename rpm-python3 to python3-rpm
- switch copr-sources-custom to python3 shebang
- keep tmpfs data mounted acros mock invocations for custom method

* Mon Feb 19 2018 clime <clime@redhat.com> 0.16-1
- new custom source method

* Sun Feb 18 2018 clime <clime@redhat.com> 0.15-1
- add support for fetch_sources_only in task defition
- allow building rpms from srpms fetched by providers, 
- extend cmdline with scm submode
- optionally set a priority for a repo
- add test for create_rpmmacros + refactoring
- allow only https and ftps protocols for source fetch

* Thu Jan 11 2018 clime <clime@redhat.com> 0.14-1
- copy out dnf and yum logs when using mock
- introspection and --version argument

* Mon Dec 11 2017 clime <clime@redhat.com> 0.13-1
- update man pages
- update help
- exclude 'tests' in package auto-discovery
- don't install additional stuff into bootstrap of custom buildroot
- Bug 1514221 - Copr fails to clone the repository. Build fails.

* Thu Nov 09 2017 clime <clime@redhat.com> 0.12-1
- fix get_mock_uniqueext call
- fortify make_srpm
- add '--private-users=pick' to make_srpm container to improve
  security
- compatibility with rpkg-client-0.11
- add config for src.stg.fedoraproject.org into default rpmbuild
  config
- fix download url for new rpkg-client version

* Wed Oct 18 2017 clime <clime@redhat.com> 0.11-1
- provide option to root spec file path in SCM with '/'
- fix exception raising in scm provider
- make command debug info nicer
- print task structure in the beginning even without -v
- add listdir after srpm production
- some Git backends do not support --depth
- remove unused run method
- checkout master by default
- with limited depth, we need to clone with --no-single-branch
- remove original perl script and mock config for it
- remove no longer needed options from rpkg.conf.j2
- SCM source types unification
- apply continuing line filtering from f4561c149893
- increase clone depth to address pag#129 SCM source type error

* Tue Sep 26 2017 clime <clime@redhat.com> 0.10-1
- use https for copr frontend in default config
- Make error message when the build task does not exist more user-
  friendly
- add --build-id switch instead of positional argument
- do not fail when lockfile does not exist
- change arguments to build_id and chroot
- remove lockfile import
- remove unused requires:
- remove unused variables in try-excepts
- #138 FileExistsError: [Errno 17] File exists: '/var/lib/copr-
  rpmbuild/lockfile.lock'

* Fri Sep 15 2017 clime <clime@redhat.com> 0.9-1
- copy spec file to the result dir to have a quick overview on the
  package

* Thu Sep 14 2017 clime <clime@redhat.com> 0.8-1
- provide more verbose exception logging
- take timeout into account
- fix downstream/upstream condition
- set also use_host_resolv to False if enable_net is False
- when building rpms, prebuild srpm in mock chroot

* Thu Sep 07 2017 clime <clime@redhat.com> 0.7-1
- rewrite to python
- build-srpm from upstream ability added
* Fri Jul 07 2017 clime <clime@redhat.com> 0.6-1
- support for source downloading

* Tue Jun 27 2017 clime <clime@redhat.com> 0.5-1
- use Perl Virtual naming for Requires

* Fri Jun 23 2017 clime <clime@redhat.com> 0.4-1
- use dnf.conf for custom-1 chroots
- also copy .spec to the build result directory
- raise curl timeout for downloading sources to be built
- changes according to review bz#1460630
- rpmbuild_networking option is now used to enable/disable net

* Wed Jun 14 2017 clime <clime@redhat.com> 0.3-1
- support for mock's bootstrap container
- check each line of sources file separately
- allow multiple sources and use current dir for mock as source dir
- also check for value of repos first before array referencing in mockcfg.tmpl
- handle null for buildroot_pkgs in mockcfg.tmpl

* Fri Jun 09 2017 clime <clime@redhat.com> 0.2-1
- new package built with tito

* Fri Jun 02 2017 clime <clime@redhat.com> 0.1-1
- Initial version

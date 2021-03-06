# coding: utf-8

import datetime
import time
import flask
import sqlalchemy

from .. import db
from .builds_logic import BuildsLogic
from copr_common.enums import StatusEnum
from coprs import helpers
from coprs import models
from coprs import exceptions
from coprs.exceptions import ObjectNotFound, ActionInProgressException
from coprs.logic.packages_logic import PackagesLogic
from coprs.logic.actions_logic import ActionsLogic

from coprs.logic.users_logic import UsersLogic
from coprs.models import User, Copr
from .coprs_logic import CoprsLogic, CoprDirsLogic, CoprChrootsLogic, PinnedCoprsLogic


@sqlalchemy.event.listens_for(models.Copr.deleted, "set")
def unpin_projects_on_delete(copr, deleted, oldvalue, event):
    if not deleted:
        return
    PinnedCoprsLogic.delete_by_copr(copr)


class ComplexLogic(object):
    """
    Used for manipulation which affects multiply models
    """

    @classmethod
    def delete_copr(cls, copr, admin_action=False):
        """
        Delete copr and all its builds.

        :param copr:
        :param admin_action: set to True to bypass permission check
        :raises ActionInProgressException:
        :raises InsufficientRightsException:
        """

        if admin_action:
            user = copr.user
        else:
            user = flask.g.user

        builds_query = BuildsLogic.get_multiple_by_copr(copr=copr)

        if copr.persistent:
            raise exceptions.InsufficientRightsException("This project is protected against deletion.")

        for build in builds_query:
            # Don't send delete action for each build, rather send an action to delete
            # a whole project as a part of CoprsLogic.delete_unsafe() method.
            BuildsLogic.delete_build(user, build, send_delete_action=False)

        CoprsLogic.delete_unsafe(user, copr)


    @classmethod
    def delete_expired_projects(cls):
        query = (
            models.Copr.query
            .filter(models.Copr.delete_after.isnot(None))
            .filter(models.Copr.delete_after < datetime.datetime.now())
            .filter(models.Copr.deleted.isnot(True))
        )
        for copr in query.all():
            print("deleting project '{}'".format(copr.full_name))
            try:
               cls.delete_copr(copr, admin_action=True)
            except ActionInProgressException as e:
                print(e)
                print("project {} postponed".format(copr.full_name))


    @classmethod
    def fork_copr(cls, copr, user, dstname, dstgroup=None):
        forking = ProjectForking(user, dstgroup)
        created = (not bool(forking.get(copr, dstname)))
        fcopr = forking.fork_copr(copr, dstname)

        if fcopr.full_name == copr.full_name:
            raise exceptions.DuplicateException("Source project should not be same as destination")

        builds_map = {}
        srpm_builds_src = []
        srpm_builds_dst = []

        for package in copr.main_dir.packages:
            fpackage = forking.fork_package(package, fcopr)

            builds = PackagesLogic.last_successful_build_chroots(package)
            if not builds:
                continue

            for build, build_chroots in builds.items():
                fbuild = forking.fork_build(build, fcopr, fpackage, build_chroots)

                if build.result_dir:
                    srpm_builds_src.append(build.result_dir)
                    srpm_builds_dst.append(fbuild.result_dir)

                for chroot, fchroot in zip(build_chroots, fbuild.build_chroots):
                    if not chroot.result_dir:
                        continue
                    if chroot.name not in builds_map:
                        builds_map[chroot.name] = {chroot.result_dir: fchroot.result_dir}
                    else:
                        builds_map[chroot.name][chroot.result_dir] = fchroot.result_dir

        builds_map['srpm-builds'] = dict(zip(srpm_builds_src, srpm_builds_dst))

        db.session.commit()
        ActionsLogic.send_fork_copr(copr, fcopr, builds_map)
        return fcopr, created

    @staticmethod
    def get_group_copr_safe(group_name, copr_name, **kwargs):
        group = ComplexLogic.get_group_by_name_safe(group_name)
        try:
            return CoprsLogic.get_by_group_id(
                group.id, copr_name, **kwargs).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Project @{}/{} does not exist."
                        .format(group_name, copr_name))

    @staticmethod
    def get_copr_safe(user_name, copr_name, **kwargs):
        """ Get one project.

        This always return personal project. For group projects see get_group_copr_safe().
        """
        try:
            return CoprsLogic.get(user_name, copr_name, **kwargs).filter(Copr.group_id.is_(None)).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Project {}/{} does not exist."
                        .format(user_name, copr_name))

    @staticmethod
    def get_copr_by_owner_safe(owner_name, copr_name, **kwargs):
        if owner_name[0] == "@":
            return ComplexLogic.get_group_copr_safe(owner_name[1:], copr_name, **kwargs)
        return ComplexLogic.get_copr_safe(owner_name, copr_name, **kwargs)

    @staticmethod
    def get_copr_by_repo_safe(repo_url):
        copr_repo = helpers.copr_repo_fullname(repo_url)
        if not copr_repo:
            return None
        try:
            owner, copr = copr_repo.split("/")
        except:
            # invalid format, e.g. multiple slashes in copr_repo
            return None
        return ComplexLogic.get_copr_by_owner_safe(owner, copr)

    @staticmethod
    def get_copr_dir_safe(ownername, copr_dirname, **kwargs):
        try:
            return CoprDirsLogic.get_by_ownername(ownername, copr_dirname).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(message="copr dir {}/{} does not exist."
                        .format(ownername, copr_dirname))

    @staticmethod
    def get_copr_by_id_safe(copr_id):
        try:
            return CoprsLogic.get_by_id(copr_id).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Project with id {} does not exist."
                        .format(copr_id))

    @staticmethod
    def get_build_safe(build_id):
        try:
            return BuildsLogic.get_by_id(build_id).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Build {} does not exist.".format(build_id))

    @staticmethod
    def get_package_by_id_safe(package_id):
        try:
            return PackagesLogic.get_by_id(package_id).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Package {} does not exist.".format(package_id))

    @staticmethod
    def get_package_safe(copr_dir, package_name):
        try:
            return PackagesLogic.get(copr_dir.id, package_name).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Package {} in the copr_dir {} does not exist."
                .format(package_name, copr_dir))

    @staticmethod
    def get_group_by_name_safe(group_name):
        try:
            group = UsersLogic.get_group_by_alias(group_name).one()
        except sqlalchemy.orm.exc.NoResultFound:
            raise ObjectNotFound(
                message="Group {} does not exist.".format(group_name))
        return group

    @staticmethod
    def get_copr_chroot_safe(copr, chroot_name):
        try:
            chroot = CoprChrootsLogic.get_by_name_safe(copr, chroot_name)
        except (ValueError, KeyError, RuntimeError) as e:
            raise ObjectNotFound(message=str(e))

        if not chroot:
            raise ObjectNotFound(
                message="Chroot name {} does not exist.".format(chroot_name))

        return chroot

    @staticmethod
    def get_active_groups_by_user(user_name):
        names = flask.g.user.user_groups
        if names:
            query = UsersLogic.get_groups_by_names_list(names)
            return query.filter(User.name == user_name)
        else:
            return []

    @staticmethod
    def get_queue_sizes():
        importing = BuildsLogic.get_build_importing_queue(background=False).count()
        pending = BuildsLogic.get_pending_build_tasks(background=False).count()
        running = BuildsLogic.get_build_tasks(StatusEnum("running")).count()

        return dict(
            importing=importing,
            pending=pending,
            running=running,
        )

    @classmethod
    def get_coprs_permissible_by_user(cls, user):
        coprs = CoprsLogic.filter_without_group_projects(
                    CoprsLogic.get_multiple_owned_by_username(
                        flask.g.user.username, include_unlisted_on_hp=False)).all()

        for group in user.user_groups:
            coprs.extend(CoprsLogic.get_multiple_by_group_id(group.id).all())

        coprs += [perm.copr for perm in user.copr_permissions if
                  perm.get_permission("admin") == helpers.PermissionEnum("approved") or
                  perm.get_permission("builder") == helpers.PermissionEnum("approved")]

        return set(coprs)


class ProjectForking(object):
    def __init__(self, user, group=None):
        self.user = user
        self.group = group

        if group and not user.can_build_in_group(group):
            raise exceptions.InsufficientRightsException(
                "Only members may create projects in the particular groups.")

    def get(self, copr, name):
        return CoprsLogic.get_by_group_id(self.group.id, name).first() if self.group \
            else CoprsLogic.filter_without_group_projects(CoprsLogic.get(flask.g.user.name, name)).first()

    def fork_copr(self, copr, name):
        fcopr = self.get(copr, name)
        if not fcopr:
            fcopr = self.create_object(models.Copr, copr,
                                       exclude=["id", "group_id", "created_on",
                                                "scm_repo_url", "scm_api_type", "scm_api_auth_json",
                                                "persistent", "auto_prune", "contact", "webhook_secret"])

            fcopr.forked_from_id = copr.id
            fcopr.user = self.user
            fcopr.user_id = self.user.id
            fcopr.created_on = int(time.time())
            if name:
                fcopr.name = name
            if self.group:
                fcopr.group = self.group
                fcopr.group_id = self.group.id

            fcopr_dir = models.CoprDir(name=fcopr.name, copr=fcopr, main=True)

            for chroot in list(copr.copr_chroots):
                CoprChrootsLogic.create_chroot(self.user, fcopr, chroot.mock_chroot, chroot.buildroot_pkgs,
                                               chroot.repos, comps=chroot.comps, comps_name=chroot.comps_name,
                                               with_opts=chroot.with_opts, without_opts=chroot.without_opts)
            db.session.add(fcopr)
            db.session.add(fcopr_dir)

        return fcopr

    def fork_package(self, package, fcopr):
        fpackage = PackagesLogic.get(fcopr.main_dir.id, package.name).first()
        if not fpackage:
            fpackage = self.create_object(models.Package, package, exclude=["id", "copr_id", "copr_dir_id", "webhook_rebuild"])
            fpackage.copr = fcopr
            fpackage.copr_dir = fcopr.main_dir
            db.session.add(fpackage)
        return fpackage

    def fork_build(self, build, fcopr, fpackage, build_chroots):
        fbuild = self.create_object(models.Build, build, exclude=["id", "copr_id", "copr_dir_id", "package_id", "result_dir"])
        fbuild.copr = fcopr
        fbuild.package = fpackage
        fbuild.copr_dir = fcopr.main_dir
        db.session.add(fbuild)
        db.session.flush()

        fbuild.result_dir = '{:08}'.format(fbuild.id)
        fbuild.build_chroots = [self.create_object(models.BuildChroot, c, exclude=["id", "build_id", "result_dir"]) for c in build_chroots]
        for chroot in fbuild.build_chroots:
            chroot.result_dir = '{:08}-{}'.format(fbuild.id, fpackage.name)
            chroot.status = StatusEnum("forked")
        db.session.add(fbuild)
        return fbuild

    def create_object(self, clazz, from_object, exclude=list()):
        arguments = {}
        for name, column in from_object.__mapper__.columns.items():
            if not name in exclude:
                arguments[name] = getattr(from_object, name)
        return clazz(**arguments)


class BuildConfigLogic(object):

    @classmethod
    def generate_build_config(cls, copr, chroot_id):
        """ Return dict with proper build config contents """
        chroot = None
        for i in copr.copr_chroots:
            if i.mock_chroot.name == chroot_id:
                chroot = i
        if not chroot:
            return {}

        packages = "" if not chroot.buildroot_pkgs else chroot.buildroot_pkgs

        repos = [{
            "id": "copr_base",
            "baseurl": copr.repo_url + "/{}/".format(chroot_id),
            "name": "Copr repository",
        }]

        if copr.module_hotfixes:
            repos[0]["module_hotfixes"] = True

        if not copr.auto_createrepo:
            repos.append({
                "id": "copr_base_devel",
                "baseurl": copr.repo_url + "/{}/devel/".format(chroot_id),
                "name": "Copr buildroot",
            })


        repos.extend(cls.get_additional_repo_views(copr.repos_list, chroot_id))
        repos.extend(cls.get_additional_repo_views(chroot.repos_list, chroot_id))

        return {
            'project_id': copr.repo_id,
            'additional_packages': packages.split(),
            'repos': repos,
            'chroot': chroot_id,
            'use_bootstrap_container': copr.use_bootstrap_container,
            'with_opts': chroot.with_opts.split(),
            'without_opts': chroot.without_opts.split(),
        }

    @classmethod
    def get_additional_repo_views(cls, repos_list, chroot_id):
        repos = []
        for repo in repos_list:
            params = helpers.parse_repo_params(repo)
            repo_view = {
                "id": helpers.generate_repo_name(repo),
                "baseurl": helpers.pre_process_repo_url(chroot_id, repo),
                "name": "Additional repo " + helpers.generate_repo_name(repo),
            }

            copr = ComplexLogic.get_copr_by_repo_safe(repo)
            if copr and copr.module_hotfixes:
                params["module_hotfixes"] = True

            repo_view.update(params)
            repos.append(repo_view)
        return repos

    @classmethod
    def generate_additional_repos(cls, copr_chroot):
        base_repo = "copr://{}".format(copr_chroot.copr.full_name)
        repos = [base_repo] + copr_chroot.repos_list + copr_chroot.copr.repos_list
        if not copr_chroot.copr.auto_createrepo:
            repos.append("copr://{}/devel".format(copr_chroot.copr.full_name))
        return repos

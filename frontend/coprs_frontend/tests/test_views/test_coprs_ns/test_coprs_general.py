import json
import flask
import pytest
import re

from unittest import mock

from sqlalchemy import desc

from copr_common.enums import ActionTypeEnum
from coprs import app, cache, models

from coprs.logic.coprs_logic import CoprsLogic, CoprDirsLogic
from coprs.logic.actions_logic import ActionsLogic

from tests.coprs_test_case import (CoprsTestCase, TransactionDecorator,
    new_app_context)


class TestMonitor(CoprsTestCase):

    def test_regression_monitor_no_copr_returned(self, f_db, f_users, f_mock_chroots):
        # https://bugzilla.redhat.com/show_bug.cgi?id=1165284

        # commit users to the database
        self.db.session.commit()
        copr_name = u"temp"

        # trying to get monitor page for non-existing project
        res = self.tc.get("/coprs/{}/{}/monitor/".format(self.u1.name, copr_name))
        assert res.status_code == 404

        tmp_copr = models.Copr(name=copr_name, user=self.u1)
        cc = models.CoprChroot()
        cc.mock_chroot = self.mc1
        tmp_copr.copr_chroots.append(cc)

        self.db.session.add_all([tmp_copr, cc])
        self.db.session.commit()

        res = self.tc.get("/coprs/{}/{}/monitor/".format(self.u1.name, copr_name))
        assert res.status_code == 200

        self.db.session.add(CoprsLogic.delete_unsafe(self.u1, tmp_copr))
        self.db.session.commit()

        res = self.tc.get("/coprs/{}/{}/monitor/".format(self.u1.name, copr_name))
        assert res.status_code == 404


class TestCoprsShow(CoprsTestCase):

    def test_show_no_entries(self):
        assert b"No projects..." in self.tc.get("/").data

    def test_show_more_entries(self, f_users, f_coprs, f_db):
        r = self.tc.get("/")
        assert r.data.count(b'<!--copr-project-->') == 3


class TestCoprsOwned(CoprsTestCase):

    @TransactionDecorator("u3")
    def test_owned_none(self, f_users, f_coprs, f_db):
        self.db.session.add(self.u3)
        r = self.test_client.get("/coprs/{0}/".format(self.u3.name))
        assert b"No projects..." in r.data

    @TransactionDecorator("u1")
    def test_owned_one(self, f_users, f_coprs, f_db):
        self.db.session.add(self.u1)
        r = self.test_client.get("/coprs/{0}/".format(self.u1.name))
        assert r.data.count(b'<!--copr-project-->') == 1


class TestCoprNew(CoprsTestCase):
    success_string = "New project has been created successfully."

    @TransactionDecorator("u1")
    def test_copr_new_normal(self, f_users, f_mock_chroots, f_db):
        r = self.test_client.post(
            "/coprs/{0}/new/".format(self.u1.name),
            data={"name": "foo",
                  "fedora-rawhide-i386": "y",
                  "arches": ["i386"]},
            follow_redirects=True)

        assert self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.name == "foo").first()
        assert self.success_string.encode("utf-8") in r.data

        # make sure no initial build was submitted
        assert self.models.Build.query.first() is None

    @TransactionDecorator("u3")
    def test_copr_new_exists_for_another_user(self, f_users, f_coprs,
                                              f_mock_chroots, f_db):

        self.db.session.add(self.c1)
        foocoprs = len(self.models.Copr.query
                       .order_by(desc(models.Copr.created_on))
                       .filter(self.models.Copr.name == self.c1.name).all())
        assert foocoprs > 0

        r = self.test_client.post(
            "/coprs/{0}/new/".format(self.u3.name),
            data={"name": self.c1.name,
                  "fedora-rawhide-i386": "y"},
            follow_redirects=True)

        self.db.session.add(self.c1)

        assert len(self.models.Copr.query
                   .order_by(desc(models.Copr.created_on))
                   .filter(self.models.Copr.name == self.c1.name).all()) == foocoprs + 1
        assert self.success_string.encode("utf-8") in r.data

    @TransactionDecorator("u1")
    def test_copr_new_exists_for_this_user(self, f_users, f_coprs,
                                           f_mock_chroots, f_db):
        self.db.session.add(self.c1)
        foocoprs = len(self.models.Copr.query
                       .order_by(desc(models.Copr.created_on))
                       .filter(self.models.Copr.name == self.c1.name).all())
        assert foocoprs > 0

        r = self.test_client.post(
            "/coprs/{0}/new/".format(self.u1.name),
            data={"name": self.c1.name,
                  "fedora-rawhide-i386": "y"},
            follow_redirects=True)

        self.db.session.add(self.c1)
        assert len(self.models.Copr.query
                   .order_by(desc(models.Copr.created_on))
                   .filter(self.models.Copr.name == self.c1.name).all()) == foocoprs
        assert b"You already have project named" in r.data

    @TransactionDecorator("u1")
    def test_copr_new_with_initial_pkgs(self, f_users, f_mock_chroots, f_db):
        r = self.test_client.post("/coprs/{0}/new/".format(self.u1.name),
                                  data={"name": "foo",
                                        "fedora-rawhide-i386": "y",
                                        "initial_pkgs": ["http://a/f.src.rpm",
                                                         "http://a/b.src.rpm"],
                                        "build_enable_net": True,
                                        },
                                  follow_redirects=True)

        copr = self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.name == "foo").first()
        assert copr
        assert self.success_string.encode("utf-8") in r.data

        assert self.models.Build.query.first().copr == copr
        assert self.models.Build.query.first().enable_net is True
        assert copr.build_count == 1
        assert b"Initial packages were successfully submitted" in r.data


    @TransactionDecorator("u1")
    def test_copr_new_with_initial_pkgs_disabled_net(self, f_users, f_mock_chroots, f_db):
        r = self.test_client.post("/coprs/{0}/new/".format(self.u1.name),
                                  data={"name": "foo",
                                        "fedora-rawhide-i386": "y",
                                        "initial_pkgs": ["http://a/f.src.rpm",
                                                         "http://a/b.src.rpm"],
                                        "build_enable_net": None
                                        },
                                  follow_redirects=True)

        copr = self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.name == "foo").first()
        assert copr
        assert self.success_string.encode("utf-8") in r.data

        assert self.models.Build.query.first().copr == copr
        assert self.models.Build.query.first().enable_net is False
        assert copr.build_count == 1
        assert b"Initial packages were successfully submitted" in r.data

    @TransactionDecorator("u1")
    def test_copr_new_is_allowed_even_if_deleted_has_same_name(
            self, f_users, f_coprs, f_mock_chroots, f_db):

        self.db.session.add(self.c1)
        self.db.session.add(self.c1_dir)
        self.c1.deleted = True
        self.c1.user = self.u1
        CoprDirsLogic.delete_all_by_copr(self.c1)
        self.db.session.commit()

        self.db.session.add(self.c1)
        r = self.test_client.post("/coprs/{0}/new/".format(self.u1.name),
                                  data={"name": self.c1.name,
                                        "fedora-rawhide-i386": "y",
                                        "arches": ["i386"]},
                                  follow_redirects=True)

        self.c1 = self.db.session.merge(self.c1)
        self.u1 = self.db.session.merge(self.u1)
        assert len(self.models.Copr.query
                   .order_by(desc(models.Copr.created_on))
                   .filter(self.models.Copr.name == self.c1.name)
                   .filter(self.models.Copr.user == self.u1)
                   .all()) == 2
        assert self.success_string.encode("utf-8") in r.data


class TestCoprDetail(CoprsTestCase):

    def test_copr_detail_not_found(self):
        r = self.tc.get("/coprs/foo/bar/")
        assert r.status_code == 404

    def test_copr_detail_normal(self, f_users, f_coprs, f_db):
        r = self.tc.get("/coprs/{0}/{1}/".format(self.u1.name, self.c1.name))
        assert r.status_code == 200
        assert self.c1.name.encode("utf-8") in r.data

    def test_copr_detail_contains_builds(self, f_users, f_coprs,
                                         f_mock_chroots, f_builds, f_db):
        r = self.tc.get(
            "/coprs/{0}/{1}/builds/".format(self.u1.name, self.c1.name))
        assert r.data.count(b'<tr class="build') == 2

    def test_copr_detail_anonymous_doesnt_contain_permissions_table_when_no_permissions(
            self, f_users, f_coprs, f_copr_permissions, f_db):

        r = self.tc.get(
            "/coprs/{0}/{1}/permissions/".format(self.u1.name, self.c1.name))
        assert b'<!--permissions-table-->' not in r.data

    def test_copr_detail_contains_permissions_table(self, f_users, f_coprs,
                                                    f_copr_permissions, f_db):

        r = self.tc.get(
            "/coprs/{0}/{1}/permissions/".format(self.u2.name, self.c3.name))
        assert b'<!--permissions-table-->' in r.data
        assert '<td>{0}'.format(self.u3.name).encode("utf-8") in r.data
        assert '<td>{0}'.format(self.u1.name).encode("utf-8") in r.data

    @TransactionDecorator("u2")
    def test_detail_has_correct_permissions_form(self, f_users, f_coprs,
                                                 f_copr_permissions, f_db):

        self.db.session.add_all([self.u2, self.c3])
        r = self.test_client.get(
            "/coprs/{0}/{1}/permissions/".format(self.u2.name, self.c3.name))

        assert r.data.count(b"nothing") == 2
        assert b'<select id="copr_builder_1" name="copr_builder_1">' in r.data
        assert b'<select id="copr_admin_1" name="copr_admin_1">' in r.data

    def test_copr_detail_doesnt_show_cancel_build_for_anonymous(self, f_users, f_coprs, f_builds, f_db):
        r = self.tc.get(
            "/coprs/{0}/{1}/build/{2}/".format(self.u2.name, self.c2.name, self.c2.builds[0].id))
        assert b"/cancel_build/" not in r.data

    @TransactionDecorator("u1")
    def test_copr_detail_doesnt_allow_non_submitter_to_cancel_build(
            self, f_users, f_coprs, f_mock_chroots, f_builds, f_db):
        self.u1.admin = False
        self.db.session.add_all([self.u1, self.u2, self.c2])
        r = self.test_client.get(
            "/coprs/{0}/{1}/build/{2}/".format(self.u2.name, self.c2.name, self.c2.builds[0].id))
        assert b"/cancel_build/" not in r.data

    @TransactionDecorator("u2")
    def test_copr_detail_allows_submitter_to_cancel_build(
            self, f_users, f_coprs, f_mock_chroots, f_builds, f_db):

        self.db.session.add_all([self.u2, self.c2])
        r = self.test_client.get(
            "/coprs/{0}/{1}/build/{2}/".format(self.u2.name, self.c2.name, self.c2.builds[0].id))
        assert b"/cancel_build/" in r.data


class TestCoprEdit(CoprsTestCase):

    @TransactionDecorator("u1")
    def test_edit_prefills_id(self, f_users, f_coprs, f_db):
        self.db.session.add_all([self.u1, self.c1])
        r = self.test_client.get(
            "/coprs/{0}/{1}/edit/".format(self.u1.name, self.c1.name))
        # TODO: use some kind of html parsing library to look
        # for the hidden input, this ties us
        # to the precise format of the tag
        assert ('<input hidden id="id" name="id" type="hidden" value="{0}">'
                .format(self.c1.id).encode("utf-8") in r.data)


class TestCoprUpdate(CoprsTestCase):

    @TransactionDecorator("u1")
    def test_update_no_changes(self, f_users, f_coprs, f_mock_chroots, f_db):
        self.db.session.add_all([self.u1, self.c1])
        r = self.test_client.post("/coprs/{0}/{1}/update/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"name": self.c1.name,
                                        "fedora-18-x86_64": "y",
                                        "id": self.c1.id},
                                  follow_redirects=True)

        assert b"Project has been updated successfully" in r.data

    @TransactionDecorator("u1")
    def test_copr_admin_can_update(self, f_users, f_coprs,
                                   f_copr_permissions, f_mock_chroots, f_db):

        self.db.session.add_all([self.u2, self.c3])
        r = self.test_client.post("/coprs/{0}/{1}/update/"
                                  .format(self.u2.name, self.c3.name),
                                  data={"name": self.c3.name,
                                        "fedora-rawhide-i386": "y",
                                        "id": self.c3.id},
                                  follow_redirects=True)

        assert b"Project has been updated successfully" in r.data

    @TransactionDecorator("u1")
    def test_update_multiple_chroots(self, f_users, f_coprs,
                                     f_copr_permissions, f_mock_chroots, f_db):

        self.db.session.add_all(
            [self.u1, self.c1, self.mc1, self.mc2, self.mc3])
        r = self.test_client.post("/coprs/{0}/{1}/update/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"name": self.c1.name,
                                        self.mc2.name: "y",
                                        self.mc3.name: "y",
                                        "id": self.c1.id},
                                  follow_redirects=True)

        assert b"Project has been updated successfully" in r.data
        self.c1 = self.db.session.merge(self.c1)
        self.mc1 = self.db.session.merge(self.mc1)
        self.mc2 = self.db.session.merge(self.mc2)
        self.mc3 = self.db.session.merge(self.mc3)

        mock_chroots = (self.models.MockChroot.query
                        .join(self.models.CoprChroot)
                        .filter(self.models.CoprChroot.copr_id ==
                                self.c1.id).all())

        mock_chroots_names = map(lambda x: x.name, mock_chroots)
        assert self.mc2.name in mock_chroots_names
        assert self.mc3.name in mock_chroots_names
        assert self.mc1.name not in mock_chroots_names

    @TransactionDecorator("u2")
    def test_update_deletes_multiple_chroots(self, f_users, f_coprs,
                                             f_copr_permissions,
                                             f_mock_chroots, f_db):

        # https://fedorahosted.org/copr/ticket/42
        self.db.session.add_all([self.u2, self.c2, self.mc1])
        # add one more mock_chroot, so that we can remove two
        self.db.session.add(self.models.CoprChroot(copr_id=self.c2.id, mock_chroot=self.mc1))
        self.db.session.commit()

        r = self.test_client.post("/coprs/{0}/{1}/update/"
                                  .format(self.u2.name, self.c2.name),
                                  data={"name": self.c2.name,
                                        self.mc1.name: "y",
                                        "id": self.c2.id},
                                  follow_redirects=True)

        assert b"Project has been updated successfully" in r.data
        self.c2 = self.db.session.merge(self.c2)
        self.mc1 = self.db.session.merge(self.mc1)
        mock_chroots = (self.models.MockChroot.query
                        .join(self.models.CoprChroot)
                        .filter(self.models.CoprChroot.copr_id ==
                                self.c2.id).all())

        assert len(mock_chroots) == 1

    @TransactionDecorator("u1")
    def test_re_enable_auto_createrepo_produce_action(
            self, f_users, f_coprs, f_mock_chroots, f_db):

        self.db.session.add_all(
            [self.u1, self.c1, self.mc1, self.mc2, self.mc3])

        username = self.u1.name
        coprname = self.c1.name
        copr_id = self.c1.id
        chroot = self.mc1.name

        # 1.ensure ACR enabled

        self.db.session.commit()
        c1_actual = CoprsLogic.get(self.u1.name, self.c1.name).one()
        assert c1_actual.auto_createrepo
        # 1. disabling ACR
        self.test_client.post(
            "/coprs/{0}/{1}/update/".format(username, coprname),
            data={"name": coprname, chroot: "y", "id": copr_id,
                  "disable_createrepo": True},
            follow_redirects=True
        )
        self.db.session.commit()

        # check current status
        c1_actual = CoprsLogic.get(username, coprname).one()
        assert not c1_actual.auto_createrepo
        # no actions issued before
        assert len(ActionsLogic.get_many().all()) == 0

        # 2. enabling ACR
        self.test_client.post(
            "/coprs/{0}/{1}/update/".format(username, coprname),
            data={"name": coprname, chroot: "y", "id": copr_id,
                  "disable_createrepo": "false"},
            follow_redirects=True
        )
        self.db.session.commit()

        c1_actual = CoprsLogic.get(username, coprname).one()

        # ACR enabled
        assert c1_actual.auto_createrepo
        # added action
        assert len(ActionsLogic.get_many().all()) > 0
        action = ActionsLogic.get_many(action_type=ActionTypeEnum("createrepo")).one()

        data_dict = json.loads(action.data)
        assert data_dict["ownername"] == username
        assert data_dict["projectname"] == coprname


class TestCoprApplyForPermissions(CoprsTestCase):

    @TransactionDecorator("u2")
    def test_apply(self, f_users, f_coprs, f_db):
        self.db.session.add_all([self.u1, self.u2, self.c1])
        r = self.test_client.post("/coprs/{0}/{1}/permissions_applier_change/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"copr_builder": "1"},
                                  follow_redirects=True)

        assert b"Successfully updated" in r.data

        self.u1 = self.db.session.merge(self.u1)
        self.u2 = self.db.session.merge(self.u2)
        self.c1 = self.db.session.merge(self.c1)
        new_perm = (self.models.CoprPermission.query
                    .filter(self.models.CoprPermission.user_id == self.u2.id)
                    .filter(self.models.CoprPermission.copr_id == self.c1.id)
                    .first())

        assert new_perm.copr_builder == 1
        assert new_perm.copr_admin == 0

    @TransactionDecorator("u1")
    def test_apply_doesnt_lower_other_values_from_admin_to_request(
            self, f_users, f_coprs, f_copr_permissions, f_db):

        self.db.session.add_all([self.u1, self.u2, self.cp1, self.c2])
        r = self.test_client.post("/coprs/{0}/{1}/permissions_applier_change/"
                                  .format(self.u2.name, self.c2.name),
                                  data={"copr_builder": 1, "copr_admin": "1"},
                                  follow_redirects=True)
        assert b"Successfully updated" in r.data

        self.u1 = self.db.session.merge(self.u1)
        self.c2 = self.db.session.merge(self.c2)
        new_perm = (self.models.CoprPermission.query
                    .filter(self.models.CoprPermission.user_id == self.u1.id)
                    .filter(self.models.CoprPermission.copr_id == self.c2.id)
                    .first())

        assert new_perm.copr_builder == 2
        assert new_perm.copr_admin == 1


class TestCoprUpdatePermissions(CoprsTestCase):

    @TransactionDecorator("u2")
    def test_cancel_permission(self, f_users, f_coprs,
                               f_copr_permissions, f_db):

        self.db.session.add_all([self.u2, self.c2])
        r = self.test_client.post("/coprs/{0}/{1}/update_permissions/"
                                  .format(self.u2.name, self.c2.name),
                                  data={"copr_builder_1": "0"},
                                  follow_redirects=True)

        # very volatile, but will fail fast if something changes
        check_string = (
            '<select id="copr_builder_1" name="copr_builder_1">'
            '<option value="0">nothing</option><option value="1">request</option>'
            '<option selected value="2">approved</option>'
            '</select>'
        )
        assert check_string.encode("utf-8") not in r.data

    @TransactionDecorator("u2")
    def test_update_more_permissions(self, f_users, f_coprs,
                                     f_copr_permissions, f_db):

        self.db.session.add_all([self.u2, self.c3])
        self.test_client.post("/coprs/{0}/{1}/update_permissions/"
                              .format(self.u2.name, self.c3.name),
                              data={"copr_builder_1": "2",
                                    "copr_admin_1": "1",
                                    "copr_admin_3": "2"},
                              follow_redirects=True)

        self.u1 = self.db.session.merge(self.u1)
        self.u3 = self.db.session.merge(self.u3)
        self.c3 = self.db.session.merge(self.c3)

        u1_c3_perms = (self.models.CoprPermission.query
                       .filter(self.models.CoprPermission.copr_id ==
                               self.c3.id)
                       .filter(self.models.CoprPermission.user_id ==
                               self.u1.id)
                       .first())

        assert (u1_c3_perms.copr_builder ==
                self.helpers.PermissionEnum("approved"))
        assert (u1_c3_perms.copr_admin ==
                self.helpers.PermissionEnum("request"))

        u3_c3_perms = (self.models.CoprPermission.query
                       .filter(self.models.CoprPermission.copr_id ==
                               self.c3.id)
                       .filter(self.models.CoprPermission.user_id ==
                               self.u3.id)
                       .first())
        assert (u3_c3_perms.copr_builder ==
                self.helpers.PermissionEnum("nothing"))
        assert (u3_c3_perms.copr_admin ==
                self.helpers.PermissionEnum("approved"))

    @TransactionDecorator("u1")
    def test_copr_admin_can_update_permissions(self, f_users, f_coprs,
                                               f_copr_permissions, f_db):

        self.db.session.add_all([self.u2, self.c3])
        r = self.test_client.post("/coprs/{0}/{1}/update_permissions/"
                                  .format(self.u2.name, self.c3.name),
                                  data={"copr_builder_1": "2",
                                        "copr_admin_3": "2"},
                                  follow_redirects=True)

        assert b"Project permissions were updated" in r.data

    @TransactionDecorator("u1")
    def test_copr_admin_can_give_up_his_permissions(self, f_users, f_coprs,
                                                    f_copr_permissions, f_db):
        # if admin is giving up his permission and there are more permissions for
        # this copr, then if the admin is altered first, he won"t be permitted
        # to alter the other permissions and the whole update would fail
        self.db.session.add_all([self.u2, self.c3, self.cp2, self.cp3])
        # mock out the order of CoprPermission objects, so that we are sure
        # the admin is the first one and therefore this fails if
        # the view doesn"t reorder the permissions

        # flexmock(self.models.Copr, copr_permissions=[self.cp3, self.cp2])
        r = self.test_client.post("/coprs/{0}/{1}/update_permissions/"
                                  .format(self.u2.name, self.c3.name),
                                  data={"copr_admin_1": "1",
                                        "copr_admin_3": "1"},
                                  follow_redirects=True)

        self.u1 = self.db.session.merge(self.u1)
        self.c3 = self.db.session.merge(self.c3)
        perm = (self.models.CoprPermission.query
                .filter(self.models.CoprPermission.user_id == self.u1.id)
                .filter(self.models.CoprPermission.copr_id == self.c3.id)
                .first())

        assert perm.copr_admin == 1
        assert b"Project permissions were updated" in r.data


class TestCoprDelete(CoprsTestCase):

    @TransactionDecorator("u1")
    def test_delete(self, f_users, f_coprs, f_db):
        self.db.session.add_all([self.u1, self.c1])
        r = self.test_client.post("/coprs/{0}/{1}/delete/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"verify": "yes"},
                                  follow_redirects=True)

        assert b"Project has been deleted successfully" in r.data
        self.db.session.add(self.c1)
        assert self.models.Action.query.first().id == self.c1.id
        assert self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.id == self.c1.id).first().deleted

    @TransactionDecorator("u1")
    def test_copr_delete_does_not_delete_if_verify_filled_wrongly(
            self, f_users, f_coprs, f_db):

        self.db.session.add_all([self.u1, self.c1])
        r = self.test_client.post("/coprs/{0}/{1}/delete/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"verify": "no"},
                                  follow_redirects=True)

        assert b"Project has been deleted successfully" not in r.data
        assert not self.models.Action.query.first()
        assert self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.id == self.c1.id).first()

    @TransactionDecorator("u2")
    def test_non_user_cant_delete(self, f_users, f_coprs, f_db):
        self.db.session.add_all([self.u1, self.u2, self.c1])
        r = self.test_client.post("/coprs/{0}/{1}/delete/"
                                  .format(self.u1.name, self.c1.name),
                                  data={"verify": "yes"},
                                  follow_redirects=True)
        self.c1 = self.db.session.merge(self.c1)
        assert b"Project has been deleted successfully" not in r.data
        assert not self.models.Action.query.first()
        assert self.models.Copr.query\
            .order_by(desc(models.Copr.created_on))\
            .filter(self.models.Copr.id == self.c1.id).first()


class TestCoprRepoGeneration(CoprsTestCase):

    """
    Requires f_mock_chroots
    """
    @pytest.fixture
    def f_custom_builds(self):
        """ Custom builds are used in order not to break the default ones """
        self.b5 = self.models.Build(
            copr=self.c1, copr_dir=self.c1_dir,
            user=self.u1, submitted_on=9,
            result_dir="bar")
        self.b6 = self.models.Build(
            copr=self.c1, copr_dir=self.c1_dir,
            user=self.u1, submitted_on=11)
        self.b7 = self.models.Build(
            copr=self.c1, copr_dir=self.c1_dir,
            user=self.u1, submitted_on=10,
            result_dir="bar")

        # assign with chroots
        for build in [self.b5, self.b6, self.b7]:
            self.db.session.add(
                self.models.BuildChroot(
                    build=build,
                    mock_chroot=self.mc1
                )
            )

        self.db.session.add_all(
            [self.b5, self.b6, self.b7])

    @pytest.fixture
    def f_not_finished_builds(self):
        """ Custom builds are used in order not to break the default ones """
        self.b8 = self.models.Build(
            copr=self.c1, user=self.u1, submitted_on=11)
        self.mc1 = self.models.MockChroot(
            os_release="fedora", os_version="18", arch="x86_64")
        self.cc1 = self.models.CoprChroot(mock_chroot=self.mc1, copr=self.c1)

        # assign with chroot
        self.db.session.add(
            self.models.BuildChroot(
                build=self.b8,
                mock_chroot=self.mc1
            )
        )

        self.db.session.add_all([self.b8, self.mc1, self.cc1])

    def test_fail_on_nonexistent_copr(self):
        r = self.tc.get(
            "/coprs/bogus-user/bogus-nonexistent-repo/repo/fedora-18-x86_64/")
        assert r.status_code == 404
        assert b"does not exist" in r.data

    def test_works_on_older_builds(self, f_users, f_coprs, f_mock_chroots,
                                   f_custom_builds, f_db):
        from coprs import app
        orig = app.config["ENFORCE_PROTOCOL_FOR_BACKEND_URL"]
        app.config["ENFORCE_PROTOCOL_FOR_BACKEND_URL"] = "https"
        r = self.tc.get(
            "/coprs/{0}/{1}/repo/fedora-18/"
            .format(self.u1.name, self.c1.name))

        assert r.status_code == 200
        assert b"baseurl=https://" in r.data
        app.config["ENFORCE_PROTOCOL_FOR_BACKEND_URL"] = orig

    @new_app_context
    def test_repofile_multilib(self, f_users, f_coprs, f_mock_chroots,
                               f_mock_chroots_many, f_custom_builds, f_db):

        r_non_ml_chroot = self.tc.get(
            "/coprs/{0}/{1}/repo/fedora-18/some.repo?arch=x86_64".format(
                self.u1.name, self.c1.name))

        for f_version in range(19, 24):
            for arch in ['x86_64', 'i386']:
                # with disabled multilib there's no change between fedora repos,
                # no matter what the version or architecture is
                r_ml_chroot = self.tc.get(
                    "/coprs/{0}/{1}/repo/fedora-{2}/some.repo?arch={3}".format(
                        self.u1.name, self.c1.name, f_version, arch))
                assert r_ml_chroot.data == r_non_ml_chroot.data

        self.c1.multilib = True
        self.db.session.commit()

        cache.clear() # f18 repofile is cached

        # The project is now multilib, but f18 chroot doesn't have i386
        # countepart in c1

        r_non_ml_chroot = self.tc.get(
            "/coprs/{0}/{1}/repo/fedora-18/some.repo?arch=x86_64".format(
                self.u1.name, self.c1.name))

        r_ml_first_chroot = self.tc.get(
            "/coprs/{0}/{1}/repo/fedora-19/some.repo?arch=x86_64".format(
                self.u1.name, self.c1.name))

        for f_version in range(19, 24):
            # All the Fedora 19..23 chroots have both i386 and x86_64 enabled in
            # c1, so all the repofiles need to be the same.
            r_ml_chroot = self.tc.get(
                "/coprs/{0}/{1}/repo/fedora-{2}/some.repo?arch=x86_64".format(
                    self.u1.name, self.c1.name, f_version))
            assert r_ml_chroot.data == r_ml_first_chroot.data
            assert r_ml_chroot.data != r_non_ml_chroot.data

            # and the non-ml variants need to match non-ml chroot f18
            # (this also checks that we don't cache 'some.repo' requests with
            # 'some.repo&arch=...')
            r_non_ml_repofile = self.tc.get(
                "/coprs/{0}/{1}/repo/fedora-{2}/some.repo".format(
                    self.u1.name, self.c1.name, f_version))
            assert r_non_ml_repofile.data == r_non_ml_chroot.data

        def parse_repofile(string):
            lines = string.split('\n')
            def get_params(name, lines):
                return [x.split('=')[1] for x in lines
                        if re.match(r'^{}=.*'.format(name), x)]
            return (
                [x.strip('[]') for x in lines if re.match(r'^\[.*\]$', x)],
                get_params('baseurl', lines),
                get_params('gpgkey', lines),
                get_params('name', lines),
                get_params('cost', lines),
            )

        non_ml_repofile = r_non_ml_chroot.data.decode('utf-8')
        ml_repofile = r_ml_first_chroot.data.decode('utf-8')

        repoids, baseurls, gpgkeys, _, costs = parse_repofile(non_ml_repofile)
        assert len(repoids) == len(baseurls) == len(gpgkeys) == 1
        assert len(costs) == 0

        normal_gpgkey = gpgkeys[0]
        normal_repoid = repoids[0]
        normal_baseurl = baseurls[0]

        repoids, baseurls, gpgkeys, names, costs = parse_repofile(ml_repofile)
        assert len(repoids) == len(baseurls) == len(gpgkeys) == 2
        assert len(costs) == 1
        assert costs[0] == '1100'

        assert normal_repoid == repoids[0]
        assert normal_repoid + ':ml' == repoids[1]
        assert 'x86_64' not in names[0]
        assert '(i386)' not in names[0]
        assert '(i386)' in names[1]
        assert gpgkeys[0] == gpgkeys[1] == normal_gpgkey
        assert normal_baseurl == baseurls[0]
        assert normal_baseurl.rsplit('-', 1)[0] == baseurls[1].rsplit('-', 1)[0]


class TestSearch(CoprsTestCase):

    @mock.patch("coprs.views.coprs_ns.coprs_general.render_template")
    def test_search_basic(self, mc_render_template, f_users, f_db):
        # mc_flask.render_template.return_value = mock.MagicMock()
        # self.prefix = u"prefix_{}_".format(int(time.time()))
        self.prefix = u"prefix"
        self.s_coprs = []

        for x in range(5):
            self.s_coprs.append(models.Copr(name=self.prefix + str(x), user=self.u1))

        for x in range(7):
            self.s_coprs.append(models.Copr(name=self.prefix + str(x), user=self.u2))

        self.db.session.add_all(self.s_coprs)
        self.db.session.commit()

        mc_render_template.side_effect = lambda *args, **kwargs: flask.render_template(*args, **kwargs)

        # self.tc.get("/coprs/fulltext/?fulltext={}".format(self.prefix))
        # qargs, qkwargs = mc_render_template.call_args
        # assert qkwargs["paginator"].total_count == 5+7
        #
        # self.tc.get("/coprs/fulltext/?fulltext={}".format("user1/prefix"))
        # qargs, qkwargs = mc_render_template.call_args
        # assert qkwargs["paginator"].total_count == 5
        #
        # self.tc.get("/coprs/fulltext/?fulltext={}".format("user1"))
        # qargs, qkwargs = mc_render_template.call_args
        # assert qkwargs["paginator"].total_count == 5
        #
        # self.tc.get("/coprs/fulltext/?fulltext={}".format("user1/"))
        # qargs, qkwargs = mc_render_template.call_args
        # assert qkwargs["paginator"].total_count == 5


class TestRepo(CoprsTestCase):
    def test_repo_renders_http(self, f_users, f_coprs, f_mock_chroots, f_db):
        url = "/coprs/{user}/{copr}/repo/{chroot}/{user}-{copr}-{chroot}.repo".format(
            user = self.u1.username,
            copr = self.c1.name,
            chroot = "{}-{}".format(self.mc1.os_release, self.mc1.os_version),
        )
        app.config["REPO_NO_SSL"] = True
        app.config["ENFORCE_PROTOCOL_FOR_BACKEND_URL"] = "https"
        with app.app_context():
            res = self.tc.get(url)
        assert res.status_code == 200
        assert 'baseurl=http://' in res.data.decode('utf-8')

    def test_chroot_alias(self, f_users, f_coprs, f_mock_chroots, f_db):
        # Test a chroot alias feature on a real-world example (RhBug: 1756632)

        mc_kwargs = dict(os_version="8", arch="x86_64", is_active=True,
                         distgit_branch=models.DistGitBranch(name="bar"))
        mc_epel = models.MockChroot(os_release="epel", **mc_kwargs)
        mc_rhelbeta = models.MockChroot(os_release="rhelbeta", **mc_kwargs)

        cc_epel = models.CoprChroot(mock_chroot=mc_epel)
        cc_rhelbeta = models.CoprChroot(mock_chroot=mc_rhelbeta)

        self.c1.copr_chroots = [cc_epel, cc_rhelbeta]
        self.db.session.commit()

        app.config["BACKEND_BASE_URL"] = "https://foo"
        with app.app_context():
            kwargs = dict(user = self.u1.username, copr = self.c1.name)
            url = "/coprs/{user}/{copr}/repo/{chroot}/"

            # Both chroots enabled, without alias
            r1 = self.tc.get(url.format(chroot="epel-8", **kwargs))
            r2 = self.tc.get(url.format(chroot="rhelbeta-8", **kwargs))
            assert "baseurl=https://foo/results/user1/foocopr/epel-8-$basearch/" in r1.data.decode("utf-8")
            assert "baseurl=https://foo/results/user1/foocopr/rhelbeta-8-$basearch/" in r2.data.decode("utf-8")

            # Both chroots enabled, alias defined
            app.config["CHROOT_NAME_RELEASE_ALIAS"] = {"epel-8": "rhelbeta-8"}
            r1 = self.tc.get(url.format(chroot="epel-8", **kwargs))
            r2 = self.tc.get(url.format(chroot="rhelbeta-8", **kwargs))
            assert "baseurl=https://foo/results/user1/foocopr/epel-8-$basearch/" in r1.data.decode("utf-8")
            assert "baseurl=https://foo/results/user1/foocopr/rhelbeta-8-$basearch/" in r2.data.decode("utf-8")

            # Only one chroot enabled, alias defined
            self.c1.copr_chroots = [cc_rhelbeta]
            self.db.session.commit()
            cache.clear()
            r1 = self.tc.get(url.format(chroot="epel-8", **kwargs))
            r2 = self.tc.get(url.format(chroot="rhelbeta-8", **kwargs))
            assert "baseurl=https://foo/results/user1/foocopr/rhelbeta-8-$basearch/" in r1.data.decode("utf-8")
            assert "baseurl=https://foo/results/user1/foocopr/rhelbeta-8-$basearch/" in r2.data.decode("utf-8")

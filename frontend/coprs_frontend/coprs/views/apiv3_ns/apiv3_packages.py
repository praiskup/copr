import flask
import wtforms
from . import query_params, pagination, get_copr, Paginator
from .json2form import get_build_form_factory
from coprs.exceptions import ApiError
from coprs.exceptions import ApiError, InsufficientRightsException, ActionInProgressException, NoPackageSourceException
from coprs.views.misc import api_login_required
from coprs import db, models, forms
from coprs.views.apiv3_ns import apiv3_ns
from coprs.logic.packages_logic import PackagesLogic

# @TODO if we need to do this on several places, we should figure a better way to do it
from coprs.views.apiv3_ns.apiv3_builds import to_dict as build_to_dict

# @TODO Don't import things from APIv1
from coprs.views.api_ns.api_general import process_package_add_or_edit


def to_dict(package):
    # @TODO review the fields
    api_keys = ["id", "name", "enable_net", "old_status", "source_type", "webhook_rebuild"]
    package_dict = {k: v for k, v in package.to_dict().items() if k in api_keys}
    package_dict.update({
        "copr": package.copr.name,
        "owner": package.copr.owner_name,
        "source": package.source_json_dict,
    })
    return package_dict


@apiv3_ns.route("/package", methods=["GET"])
@query_params()
def get_package(ownername, projectname, packagename):
    copr = get_copr(ownername, projectname)
    try:
        package = PackagesLogic.get(copr.id, packagename)[0]
    except IndexError:
        raise ApiError("No package with name {name} in copr {copr}".format(name=packagename, copr=copr.name))
    return flask.jsonify(to_dict(package))


@apiv3_ns.route("/package/list/", methods=["GET"])
@pagination()
@query_params()
def get_package_list(ownername, projectname, **kwargs):
    copr = get_copr(ownername, projectname)
    paginator = Paginator(PackagesLogic.get_all(copr.id), models.Package, **kwargs)
    packages = paginator.map(to_dict)
    return flask.jsonify(items=packages, meta=paginator.meta)


@apiv3_ns.route("/package/add", methods=["POST"])
@api_login_required
def package_add():
    copr = get_copr()
    form = forms.PackageTypeSelectorForm()
    process_package_add_or_edit(copr, form.source_type_text.data)
    package = PackagesLogic.get(copr.id, form.package_name.data).first()
    return flask.jsonify(to_dict(package))


@apiv3_ns.route("/package/edit", methods=["POST"])
@api_login_required
def package_edit():
    copr = get_copr()
    form = forms.PackageTypeSelectorForm()
    try:
        package = PackagesLogic.get(copr.id, form.package_name.data)[0]
    except IndexError:
        raise ApiError("Package {name} does not exists in copr {copr}."
                             .format(name=form.package_name.data, copr=copr.full_name))

    process_package_add_or_edit(copr, form.source_type_text.data, package=package)
    return flask.jsonify(to_dict(package))


@apiv3_ns.route("/package/reset", methods=["POST"])
@api_login_required
def package_reset():
    copr = get_copr()
    form = forms.BasePackageForm()
    try:
        package = PackagesLogic.get(copr.id, form.package_name.data)[0]
    except IndexError:
        raise ApiError("No package with name {name} in copr {copr}"
                       .format(name=form.package_name.data, copr=copr.name))
    try:
        PackagesLogic.reset_package(flask.g.user, package)
        db.session.commit()
    except InsufficientRightsException as e:
        raise ApiError(str(e))

    return flask.jsonify(to_dict(package))


@apiv3_ns.route("/package/build", methods=["POST"])
@api_login_required
def package_build():
    copr = get_copr()
    form = get_build_form_factory(forms.RebuildPackageFactory.create_form_cls, copr.active_chroots)
    try:
        package = PackagesLogic.get(copr.id, form.package_name.data)[0]
    except IndexError:
        raise ApiError("No package with name {name} in copr {copr}"
                             .format(name=form.package_name.data, copr=copr.name))
    if form.validate_on_submit():
        try:
            build = PackagesLogic.build_package(flask.g.user, copr, package, form.selected_chroots, **form.data)
            db.session.commit()
        except (InsufficientRightsException, ActionInProgressException, NoPackageSourceException) as e:
            raise ApiError(str(e))
    else:
        raise ApiError(form.errors)
    return flask.jsonify(build_to_dict(build))


@apiv3_ns.route("/package/delete", methods=["POST"])
@api_login_required
def package_delete():
    copr = get_copr()
    form = forms.BasePackageForm()
    try:
        package = PackagesLogic.get(copr.id, form.package_name.data)[0]
    except IndexError:
        raise ApiError("No package with name {name} in copr {copr}"
                             .format(name=form.package_name.data, copr=copr.name))

    try:
        PackagesLogic.delete_package(flask.g.user, package)
        db.session.commit()
    except (InsufficientRightsException, ActionInProgressException) as e:
        raise ApiError(str(e))

    return flask.jsonify(to_dict(package))

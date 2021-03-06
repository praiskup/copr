import click
from coprs import db_session_scope
from coprs.logic.builds_logic import BuildsLogic


@click.command()
def clean_old_builds():
    """
    This garbage collects all builds which are "obsoleted" per user
    configuration, per models.Package.max_builds configuration.
    """
    with db_session_scope():
        BuildsLogic.clean_old_builds()

import logging
from ..helpers import run_cmd
from .base import Provider

log = logging.getLogger("__main__")


class RubyGemsProvider(Provider):
    def __init__(self, source_json, outdir, config=None):
        super(RubyGemsProvider, self).__init__(source_json, outdir, config)
        self.gem_name = source_json["gem_name"]

    def tool_presence_check(self):
        try:
            run_cmd(["which", "gem2rpm"])
        except RuntimeError as err:
            log.error("Please, install gem2rpm.")
            raise err

    def produce_srpm(self):
        self.tool_presence_check()

        cmd = ["gem2rpm", self.gem_name, "--srpm", "-C", self.outdir, "--fetch"]
        result = run_cmd(cmd)

        if "Empty tag: License" in result.stderr:
            raise RuntimeError("\n".join([
                result.stderr,
                "Not specifying a license means all rights are reserved;"
                "others have no rights to use the code for any purpose.",
                "See http://guides.rubygems.org/specification-reference/#license="]))

        return result

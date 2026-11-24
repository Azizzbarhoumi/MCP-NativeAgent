"""Try the inspector by hand, without the agent:

  python -m mcp_servers.git_local eval\\git_scenarios\\build\\merge_conflict\\work
  python -m mcp_servers.git_local <path> --rate "git push --force"
"""

import argparse
import json

from mcp_servers.git_local import inspect_repo as ir
from mcp_servers.git_local.risk import classify

ap = argparse.ArgumentParser()
ap.add_argument("path")
ap.add_argument("--rate", help="also rate this command")
a = ap.parse_args()
try:
    print(json.dumps(ir.repo_state(a.path), indent=2))
    print(ir.log(a.path, 10))
except ir.GitToolError as e:
    print("refused:", e)
if a.rate:
    print(json.dumps(classify(a.rate), indent=2))

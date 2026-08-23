from __future__ import annotations
import importlib.util, json, tempfile, unittest, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("m", ROOT / "scripts/bootstrap_github_governance.py")
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = m; SPEC.loader.exec_module(m)

class FakeClient:
    def __init__(self): self.calls=[]
    def request(self, method, path, payload=None, acceptable_statuses=(200,201,204)):
        self.calls.append((method,path,payload,acceptable_statuses))
        if method=="GET" and path=="/repos/alex/repo":
            return {"id":123,"allow_merge_commit":False,"allow_rebase_merge":False}
        if method=="GET" and path.endswith("/branches/main"): return {"protected":True}
        if method=="GET" and path.endswith("/deployment-branch-policies"): return {"branch_policies":[]}
        if method=="GET" and path.endswith("/variables"): return {"variables":[{"name":"DATABRICKS_HOST"}]}
        return {}

class TestGitHubBootstrap(unittest.TestCase):
    def make_config(self, root):
        payload={"repository":"alex/repo","environments":{name:{"databricks_host":f"https://{name}.cloud.databricks.com","databricks_client_id":f"client-{name}"} for name in m.REQUIRED_ENVIRONMENTS}}
        path=root/"config.json"; path.write_text(json.dumps(payload),encoding="utf-8")
        return m.load_config(path)
    def test_protection_contract(self):
        p=m.branch_protection_payload()
        self.assertTrue(p["required_status_checks"]["strict"])
        self.assertEqual(["validate"],p["required_status_checks"]["contexts"])
        self.assertTrue(p["enforce_admins"]); self.assertTrue(p["required_linear_history"])
        self.assertFalse(p["allow_force_pushes"]); self.assertFalse(p["allow_deletions"])
    def test_apply_operations_and_verification(self):
        with tempfile.TemporaryDirectory() as d:
            client=FakeClient(); result=m.apply_governance(self.make_config(Path(d)),client=client)
        self.assertTrue(result["protected"])
        paths={(method,path) for method,path,_,_ in client.calls}
        self.assertIn(("PUT","/repos/alex/repo/branches/main/protection"),paths)
        for env in m.REQUIRED_ENVIRONMENTS:
            self.assertIn(("PUT",f"/repos/alex/repo/environments/{env}"),paths)
        writes=[c for c in client.calls if "/variables" in c[1] and c[0] in {"POST","PATCH"}]
        self.assertEqual(8,len(writes))
    def test_dry_run_redacts_values(self):
        with tempfile.TemporaryDirectory() as d:
            summary=m.dry_run_summary(self.make_config(Path(d)),required_approvals=0)
        text=json.dumps(summary)
        self.assertNotIn("client-dev-plan",text); self.assertNotIn("dev-plan.cloud.databricks.com",text)
    def test_requires_four_environments(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"bad.json"; path.write_text(json.dumps({"repository":"alex/repo","environments":{}}))
            with self.assertRaisesRegex(ValueError,"four required environments"): m.load_config(path)
if __name__=="__main__": unittest.main()

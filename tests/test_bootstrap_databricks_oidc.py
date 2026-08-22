from __future__ import annotations
import importlib.util, json, tempfile, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("m",ROOT/"scripts/bootstrap_databricks_oidc.py")
assert SPEC and SPEC.loader
m=importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = m; SPEC.loader.exec_module(m)
class FakeCli:
    def __init__(self,listed): self.listed=list(listed); self.commands=[]
    def json(self,command):
        self.commands.append(command)
        if "list" in command: return self.listed.pop(0)
        return {"policy_id":"created"}
class TestDatabricksBootstrap(unittest.TestCase):
    def make_config(self,root):
        payload={"repository":"alex/repo","account_host":"https://accounts.cloud.databricks.com","account_id":"account-1","audience":"https://github.com/alex","principals":{name:{"numeric_id":str(i+100),"application_id":f"app-{name}"} for i,name in enumerate(m.REQUIRED_ENVIRONMENTS)}}
        path=root/"config.json"; path.write_text(json.dumps(payload)); return m.load_config(path)
    def test_exact_subject(self):
        self.assertEqual("repo:alex/repo:environment:dev-plan",m.subject("alex/repo","dev-plan"))
    def test_missing_policies_created(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.make_config(Path(d)); cli=FakeCli([{"policies":[]} for _ in m.REQUIRED_ENVIRONMENTS]); result=m.ensure_policies(config,cli=cli)
        creates=[c for c in cli.commands if "create" in c]; self.assertEqual(4,len(creates))
        for command in creates:
            payload=json.loads(command[command.index("--json")+1]); self.assertEqual(m.ISSUER,payload["oidc_policy"]["issuer"])
        self.assertEqual({"created"},{p["outcome"] for p in result["policies"]})
    def test_existing_exact_policies_verified(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.make_config(Path(d)); cli=FakeCli([{"policies":[m.policy_payload(config,e)]} for e in m.REQUIRED_ENVIRONMENTS]); result=m.ensure_policies(config,cli=cli)
        self.assertFalse(any("create" in c for c in cli.commands)); self.assertEqual({"verified"},{p["outcome"] for p in result["policies"]})
    def test_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            config=self.make_config(Path(d)); sub=m.subject(config.repository,"dev-plan"); cli=FakeCli([{"policies":[{"oidc_policy":{"issuer":m.ISSUER,"audiences":["wrong"],"subject":sub}}]}])
            with self.assertRaisesRegex(RuntimeError,"conflicts"): m.ensure_policies(config,cli=cli)
    def test_dry_run_redacts_app_ids(self):
        with tempfile.TemporaryDirectory() as d: rendered=json.dumps(m.dry_run_summary(self.make_config(Path(d))))
        self.assertNotIn("app-dev-plan",rendered)
if __name__=="__main__": unittest.main()

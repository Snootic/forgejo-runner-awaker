import unittest

from awaker.execution import runner_script
from awaker.profiles import JobProfile


class ExecutionTests(unittest.TestCase):
    def test_script_quotes_arguments_and_binds_the_job_attempt(self) -> None:
        script = runner_script(
            JobProfile("node:lts"),
            "https://forgejo.example",
            ["ubuntu-latest:docker://node:lts"],
            "uuid",
            "attempt'; touch /tmp/x",
        )
        self.assertIn("--handle 'attempt'", script)
        self.assertIn("unset RUNNER_TOKEN", script)
        self.assertIn('docker_host: "-"', script)
        self.assertNotIn("forgejo-runner register", script)

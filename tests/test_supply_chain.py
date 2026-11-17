"""Lieferkette (P0-2): Abhängigkeiten gepinnt, Workflows auf Commit-SHA festgelegt.

Läuft ohne Netz. Prüft nur Dateien im Repo, damit ein ungepinnter Eintrag oder eine bewegliche Action
(`@v4`) schon im Pull Request auffällt und nicht erst in der CI-Umgebung."""
import re
import unittest

from tests._support import REPO


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_packages(path):
    """Paketname -> (Version, Anzahl Hashes) aus einer pip-compile-Lock-Datei."""
    text = path.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)((?:\s*\\\n\s+--hash=sha256:[0-9a-f]{64})*)", text, re.M):
        out[_norm(m.group(1))] = (m.group(2), len(re.findall(r"--hash=sha256:", m.group(3))))
    return out


def _requirement_names(path):
    """Namen der nicht auskommentierten Anforderungen einer requirements-Datei."""
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*([<>=~!][^#]*)?(\s+#.*)?$", line.strip())
        if m and not line.lstrip().startswith("#"):
            names.add(_norm(m.group(1)))
    return names


class TestLockFiles(unittest.TestCase):
    def test_core_lock_covers_requirements(self):
        lock = _lock_packages(REPO / "requirements.lock")
        wanted = _requirement_names(REPO / "requirements.txt")
        self.assertTrue(wanted, "requirements.txt nennt keine Pflicht-Abhängigkeit")
        self.assertLessEqual(wanted, set(lock), "requirements.lock fehlt: %s" % (wanted - set(lock)))

    def test_optional_lock_covers_optional_in(self):
        lock = _lock_packages(REPO / "requirements-optional.lock")
        wanted = _requirement_names(REPO / "requirements-optional.in")
        self.assertLessEqual(wanted, set(lock), "requirements-optional.lock fehlt: %s" % (wanted - set(lock)))

    def test_optional_in_matches_commented_hints_in_requirements_txt(self):
        """Die auskommentierten Hinweise in requirements.txt und requirements-optional.in dürfen nicht auseinanderlaufen."""
        hinted = set()
        for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#\s+([A-Za-z0-9_.-]+)\s{2,}#", line)
            if m:
                hinted.add(_norm(m.group(1)))
        self.assertTrue(hinted)
        self.assertEqual(hinted, _requirement_names(REPO / "requirements-optional.in"))

    def test_every_lock_entry_is_pinned_with_hashes(self):
        for name in ("requirements.lock", "requirements-optional.lock"):
            text = (REPO / name).read_text(encoding="utf-8")
            pkgs = _lock_packages(REPO / name)
            self.assertTrue(pkgs, name)
            for pkg, (ver, nh) in pkgs.items():
                with self.subTest(lock=name, pkg=pkg):
                    self.assertGreaterEqual(nh, 1, "%s==%s ohne Hash" % (pkg, ver))
            # keine Zeile mit Versionsbereich statt ==
            for line in text.splitlines():
                if re.match(r"^[A-Za-z0-9_.-]+\s*[<>~!]", line):
                    self.fail("nicht exakt gepinnt: " + line)

    def test_optional_lock_is_consistent_with_core_lock(self):
        core = _lock_packages(REPO / "requirements.lock")
        opt = _lock_packages(REPO / "requirements-optional.lock")
        for pkg, (ver, _) in core.items():
            with self.subTest(pkg=pkg):
                self.assertEqual(opt.get(pkg, (ver,))[0], ver, "%s: unterschiedliche Version in den Lock-Dateien" % pkg)


class TestWorkflows(unittest.TestCase):
    def _workflows(self):
        files = sorted((REPO / ".github" / "workflows").glob("*.yml"))
        self.assertTrue(files)
        return files

    def test_every_action_is_pinned_to_a_commit_sha(self):
        for f in self._workflows():
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                m = re.match(r"^\s*-?\s*uses:\s*(\S+)", line)
                if m and not m.group(1).startswith("./"):
                    with self.subTest(file=f.name, line=n):
                        self.assertRegex(m.group(1), r"@[0-9a-f]{40}$", "Action nicht auf SHA festgelegt: " + m.group(1))

    def test_ci_installs_only_from_hashed_lock(self):
        for f in self._workflows():
            for line in f.read_text(encoding="utf-8").splitlines():
                if re.search(r"pip install .*-r\s+requirements", line):
                    with self.subTest(file=f.name):
                        self.assertIn("--require-hashes", line)
                        self.assertIn("requirements.lock", line)

    def test_workflows_default_to_read_only_token(self):
        for f in self._workflows():
            with self.subTest(file=f.name):
                self.assertRegex(f.read_text(encoding="utf-8"), r"(?m)^permissions:\n\s+contents: read")

    def test_expected_workflows_exist(self):
        names = {f.name for f in self._workflows()}
        self.assertLessEqual({"tests.yml", "codeql.yml", "sbom.yml"}, names)

    def test_dependabot_covers_pip_and_actions(self):
        text = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn('package-ecosystem: "pip"', text)
        self.assertIn('package-ecosystem: "github-actions"', text)


if __name__ == "__main__":
    unittest.main()

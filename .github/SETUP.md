# GitHub Repository Setup

## Initial Setup Sequence

Follow these steps in order for the first release:

### 1. Create GitHub Repository

```bash
gh repo create xnoto/opencode-agent-hub --public --source=. --push
```

### 2. Configure PyPI Trusted Publishing (Before First Release)

> **Important**: Do this BEFORE merging the Release PR, or publish will fail.

1. Go to https://pypi.org/manage/account/publishing/
2. Under "Add a new pending publisher":
   - PyPI Project Name: `opencode-agent-hub`
   - Owner: `xnoto`
   - Repository: `opencode-agent-hub`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. Click "Add"

### 3. Create GitHub Environment

1. Go to repo **Settings** → **Environments** → **New environment**
2. Name: `pypi`
3. Click "Configure environment"
4. (Optional) Add deployment protection rules

### 4. Configure Branch Protection

1. Go to **Settings** → **Branches** → **Add branch protection rule**
2. Branch name pattern: `main`
3. Enable:
   - [x] **Require a pull request before merging**
     - [x] Require approvals: 1 (or 0 for solo projects)
   - [x] **Require status checks to pass before merging**
     - [x] Require branches to be up to date before merging
     - Add required checks:
       - `lint`
       - `test (3.11)`
       - `test (3.12)`
       - `test (3.13)`
   - [x] **Do not allow bypassing the above settings**
4. Click **Create**

### 5. Wait for Release PR

After the initial push to main, release-please will automatically:
1. Detect this is a new project
2. Create a Release PR titled "chore(main): release 0.1.0"
3. The PR will contain:
   - CHANGELOG.md (auto-generated)
   - Version confirmation

### 6. Merge Release PR

1. Review the Release PR
2. Ensure PyPI trusted publishing is configured (step 2)
3. Merge the PR
4. This triggers:
   - Git tag `v0.1.0` created
   - `publish.yml` workflow runs
   - Package published to PyPI
   - GitHub Release created

### 7. Verify

```bash
# Check PyPI
pip index versions opencode-agent-hub

# Check installation works
uv tool install opencode-agent-hub
agent-hub-daemon --help
```

---

## Ongoing Releases

After initial setup, releases are automated:

1. **Write conventional commits** on feature branches:
   - `feat(scope):` → minor bump (0.1.0 → 0.2.0)
   - `fix(scope):` → patch bump (0.1.0 → 0.1.1)
   - `feat(scope)!:` → major bump (0.1.0 → 1.0.0)

2. **Merge PRs to main** - release-please automatically:
   - Creates/updates a Release PR with version bump + CHANGELOG
   - Batches all commits since last release

3. **Merge the Release PR** when ready to publish

### Manual Release (Fallback)

If release-please fails or you need manual control:

```bash
# 1. Update version
vim src/opencode_agent_hub/__init__.py

# 2. Commit and push
git add -A
git commit -m "chore(release): bump version to X.Y.Z"
git push

# 3. Create and push tag
git tag vX.Y.Z
git push origin vX.Y.Z
```

---

## Homebrew Tap Setup

### Initial Setup

```bash
# Create the tap repo
gh repo create xnoto/homebrew-opencode-agent-hub --public
cd /path/to/homebrew-opencode-agent-hub
git add -A
git commit -m "feat(formula): initial opencode-agent-hub formula"
git push
```

### After Each PyPI Release

1. Get the SHA256:
   ```bash
   VERSION=0.1.0
   curl -sL "https://files.pythonhosted.org/packages/source/o/opencode-agent-hub/opencode_agent_hub-${VERSION}.tar.gz" | shasum -a 256
   ```

2. Update `Formula/opencode-agent-hub.rb`:
   - Update version in URL
   - Update sha256

3. Commit and push:
   ```bash
   git commit -am "feat(formula): bump to ${VERSION}"
   git push
   ```

4. Test:
   ```bash
   brew install --build-from-source ./Formula/opencode-agent-hub.rb
   ```

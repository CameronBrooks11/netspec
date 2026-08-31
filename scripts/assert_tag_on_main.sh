#!/usr/bin/env bash
# Refuse to publish a commit that is not on main.
#
# release.yml runs no tests, deliberately: it verifies the *artifact*, and
# correctness is the `Check` gate's job on every commit that reaches main.
# Without this, `git tag v9.9.9 <any-commit> && git push --tags` would publish
# code no gate ever ran against.
#
# A script rather than inline YAML because a workflow cannot be tested: grepping
# release.yml proves a string is present, not that the gate blocks anything, and
# a one-character edit (dropping the `!`, or an `exit 0`) turns an inline gate
# into a no-op that still greps clean. This runs against throwaway repositories
# in the test suite.
#
# LIMIT, stated plainly: being on main proves the commit is on main. It proves
# the gate *passed* only if main is protected to require it. netspec's main is
# not protected today, so this blocks stray tags but not an unreviewed push.
# See docs/DECISIONS.md D17.
#
# Usage: assert_tag_on_main.sh <commit-sha> [main-ref]
# Exit:  0 the commit is on main and may be published
#        1 it is not, or the question could not be answered
set -euo pipefail

commit="${1:?usage: assert_tag_on_main.sh <commit-sha> [main-ref]}"
main_ref="${2:-origin/main}"

if ! git rev-parse --verify --quiet "$main_ref" >/dev/null; then
  echo "::error::cannot resolve $main_ref, so the tag's ancestry cannot be established."
  echo "::error::Refusing to publish rather than assuming. A shallow checkout causes this;"
  echo "::error::the release job needs fetch-depth: 0."
  exit 1
fi

# --is-ancestor peels an annotated tag object to its commit, so this holds
# whether the argument is a commit or a tag object.
if git merge-base --is-ancestor "$commit" "$main_ref"; then
  echo "$commit is on $main_ref."
  exit 0
fi

echo "::error::$commit is not on $main_ref."
echo "::error::Nothing in the release workflow runs the test suite, so a tag off main"
echo "::error::would publish code the Check gate never saw."
exit 1

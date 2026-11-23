# Publishing the GitHub wiki

The Markdown pages in this directory preserve the existing
[GitHub wiki](https://github.com/xiao-villamor/PrintStash/wiki) URLs and point to
the maintained guides. Review page changes here through a pull request.

GitHub serves the live wiki from a separate Git repository. After review and
merge, a maintainer with wiki write access can publish the pages:

1. Clone `https://github.com/xiao-villamor/PrintStash.wiki.git` into a separate
   checkout. Use the wiki's default branch and check its recent history before
   replacing any pages.
2. Copy the eight page files and `_Sidebar.md` from this directory into the wiki
   checkout, keeping their filenames. Leave this publishing guide in the main
   repository.
3. Review `git diff` for updates made on the wiki since the PR was prepared.
   Preserve any newer edits that still apply. Commit the reviewed files and push
   the wiki's default branch.
4. Open the live wiki and check Home, Installation, Development, and the
   sidebar links.

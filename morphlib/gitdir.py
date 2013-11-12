# Copyright (C) 2013  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# =*= License: GPL-2 =*=


import cliapp
import itertools
import os

import morphlib


class NoWorkingTreeError(cliapp.AppException):

    def __init__(self, repo):
        cliapp.AppException.__init__(
            self, 'Git directory %s has no working tree '
                  '(is bare).' % repo.dirname)


class InvalidRefError(cliapp.AppException):
    def __init__(self, repo, ref):
        cliapp.AppException.__init__(
            self, 'Git directory %s has no commit '
                  'at ref %s.' %(repo.dirname, ref))


class ExpectedSha1Error(cliapp.AppException):

    def __init__(self, ref):
        self.ref = ref
        cliapp.AppException.__init__(
            self, 'SHA1 expected, got %s' % ref)


class RefChangeError(cliapp.AppException):
    pass


class RefAddError(RefChangeError):

    def __init__(self, gd, ref, sha1, original_exception):
        self.gd = gd
        self.dirname = dirname = gd.dirname
        self.ref = ref
        self.sha1 = sha1
        self.original_exception = original_exception
        RefChangeError.__init__(self, 'Adding ref %(ref)s '\
            'with commit %(sha1)s failed in git repository '\
            'located at %(dirname)s: %(original_exception)r' % locals())


class RefUpdateError(RefChangeError):

    def __init__(self, gd, ref, old_sha1, new_sha1, original_exception):
        self.gd = gd
        self.dirname = dirname = gd.dirname
        self.ref = ref
        self.old_sha1 = old_sha1
        self.new_sha1 = new_sha1
        self.original_exception = original_exception
        RefChangeError.__init__(self, 'Updating ref %(ref)s '\
            'from %(old_sha1)s to %(new_sha1)s failed in git repository '\
            'located at %(dirname)s: %(original_exception)r' % locals())


class RefDeleteError(RefChangeError):

    def __init__(self, gd, ref, sha1, original_exception):
        self.gd = gd
        self.dirname = dirname = gd.dirname
        self.ref = ref
        self.sha1 = sha1
        self.original_exception = original_exception
        RefChangeError.__init__(self, 'Deleting ref %(ref)s '\
            'expecting commit %(sha1)s failed in git repository '\
            'located at %(dirname)s: %(original_exception)r' % locals())


class Remote(object):
    '''Represent a remote git repository.

    This can either be nascent or concrete, depending on whether the
    name is given.

    Changes to a concrete remote's config are written-through to git's
    config files, while a nascent remote keeps changes in-memory.

    '''

    def __init__(self, gd, name=None):
        self.gd = gd
        self.name = name
        self.fetch_url = None

    def set_fetch_url(self, url):
        self.fetch_url = url
        if self.name is not None:
            self.gd._runcmd(['git', 'remote', 'set-url', self.name, url])

    def get_fetch_url(self):
        if self.name is None:
            return self.fetch_url
        try:
            # git-ls-remote is used, rather than git-config, since
            # url.*.{push,}insteadof is processed after config is loaded
            return self.gd._runcmd(
                ['git', 'ls-remote', '--get-url', self.name]).rstrip('\n')
        except cliapp.AppException:
            return None


class GitDirectory(object):

    '''Represent a git working tree + .git directory.

    This class represents a directory that is the result of a
    "git clone". It includes both the .git subdirectory and
    the working tree. It is a thin abstraction, meant to make
    it easier to do certain git operations.

    '''

    def __init__(self, dirname):
        self.dirname = dirname

    def _runcmd(self, argv, **kwargs):
        '''Run a command at the root of the git directory.

        See cliapp.runcmd for arguments.

        Do NOT use this from outside the class. Add more public
        methods for specific git operations instead.

        '''

        return cliapp.runcmd(argv, cwd=self.dirname, **kwargs)

    def checkout(self, branch_name): # pragma: no cover
        '''Check out a git branch.'''
        self._runcmd(['git', 'checkout', branch_name])

    def branch(self, new_branch_name, base_ref): # pragma: no cover
        '''Create a git branch based on an existing ref.

        This does not automatically check out the branch.

        base_ref may be None, in which case the current branch is used.

        '''

        argv = ['git', 'branch', new_branch_name]
        if base_ref is not None:
            argv.append(base_ref)
        self._runcmd(argv)

    def is_currently_checked_out(self, ref): # pragma: no cover
        '''Is ref currently checked out?'''

        # Try the ref name directly first. If that fails, prepend origin/
        # to it. (FIXME: That's a kludge, and should be fixed.)
        try:
            parsed_ref = self._runcmd(['git', 'rev-parse', ref]).strip()
        except cliapp.AppException:
            parsed_ref = self._runcmd(
                ['git', 'rev-parse', 'origin/%s' % ref]).strip()
        parsed_head = self._runcmd(['git', 'rev-parse', 'HEAD']).strip()
        return parsed_ref == parsed_head

    def get_file_from_ref(self, ref, filename): # pragma: no cover
        '''Get file contents from git by ref and filename.

        `ref` should be a tree-ish e.g. HEAD, master, refs/heads/master,
        refs/tags/foo, though SHA1 tag, commit or tree IDs are also valid.

        `filename` is the path to the file object from the base of the
        git directory.

        Returns the contents of the referred to file as a string.

        '''

        # Blob ID is left as the git revision, rather than SHA1, since
        # we know get_blob_contents will accept it
        blob_id = '%s:%s' % (ref, filename)
        return self.get_blob_contents(blob_id)

    def get_blob_contents(self, blob_id): # pragma: no cover
        '''Get file contents from git by ID'''
        return self._runcmd(
            ['git', 'cat-file', 'blob', blob_id])

    def get_commit_contents(self, commit_id): # pragma: no cover
        '''Get commit contents from git by ID'''
        return self._runcmd(
            ['git', 'cat-file', 'commit', commit_id])

    def update_submodules(self, app): # pragma: no cover
        '''Change .gitmodules URLs, and checkout submodules.'''
        morphlib.git.update_submodules(app, self.dirname)

    def set_config(self, key, value):
        '''Set a git repository configuration variable.

        The key must have at least one period in it: foo.bar for example,
        not just foo. The part before the first period is interpreted
        by git as a section name.

        '''

        self._runcmd(['git', 'config', key, value])

    def get_config(self, key):
        '''Return value for a git repository configuration variable.'''

        value = self._runcmd(['git', 'config', '-z', key])
        return value.rstrip('\0')

    def get_remote(self, *args, **kwargs):
        '''Get a remote for this Repository.

        Gets a previously configured remote if a remote name is given.
        Otherwise a nascent one is created.

        '''
        return Remote(self, *args, **kwargs)

    def update_remotes(self): # pragma: no cover
        '''Run "git remote update --prune".'''
        self._runcmd(['git', 'remote', 'update', '--prune'])

    def is_bare(self):
        '''Determine whether the repository has no work tree (is bare)'''
        return self.get_config('core.bare') == 'true'

    def list_files(self, ref=None):
        '''Return an iterable of the files in the repository.

        If `ref` is specified, list files at that ref, otherwise
        use the working tree.

        If this is a bare repository and no ref is specified, raises
        an exception.

        '''
        if ref is None and self.is_bare():
            raise NoWorkingTreeError(self)
        if ref is None:
            return self._list_files_in_work_tree()
        else:
            return self._list_files_in_ref(ref)

    def _rev_parse(self, ref):
        try:
            return self._runcmd(['git', 'rev-parse', '--verify', ref]).strip()
        except cliapp.AppException as e:
            raise InvalidRefError(self, ref)

    def resolve_ref_to_commit(self, ref):
        return self._rev_parse('%s^{commit}' % ref)

    def resolve_ref_to_tree(self, ref):
        return self._rev_parse('%s^{tree}' % ref)

    def _list_files_in_work_tree(self):
        for dirpath, subdirs, filenames in os.walk(self.dirname):
            if dirpath == self.dirname and '.git' in subdirs:
                subdirs.remove('.git')
            for filename in filenames:
                yield os.path.join(dirpath, filename)[len(self.dirname)+1:]

    def _list_files_in_ref(self, ref):
        tree = self.resolve_ref_to_tree(ref)
        output = self._runcmd(['git', 'ls-tree', '--name-only', '-rz', tree])
        # ls-tree appends \0 instead of interspersing, so we need to
        # strip the trailing \0 before splitting
        paths = output.strip('\0').split('\0')
        return paths

    def read_file(self, filename, ref=None):
        if ref is None and self.is_bare():
            raise NoWorkingTreeError(self)
        if ref is None:
            with open(os.path.join(self.dirname, filename)) as f:
                return f.read()
        tree = self.resolve_ref_to_tree(ref)
        return self.get_file_from_ref(tree, filename)

    @property
    def HEAD(self):
        output = self._runcmd(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        return output.strip()

    def get_index(self, index_file=None):
        return morphlib.gitindex.GitIndex(self, index_file)

    def store_blob(self, blob_contents):
        '''Hash `blob_contents`, store it in git and return the sha1.

        `blob_contents` must either be a string or a value suitable to
        pass to subprocess.Popen i.e. a file descriptor or file object
        with fileno() method.

        '''
        if isinstance(blob_contents, basestring):
            kwargs = {'feed_stdin': blob_contents}
        else:
            kwargs = {'stdin': blob_contents}
        return self._runcmd(['git', 'hash-object', '-t', 'blob',
                             '-w', '--stdin'], **kwargs).strip()

    def commit_tree(self, tree, parent, message, **kwargs):
        '''Create a commit'''
        # NOTE: Will need extension for 0 or N parents.
        env = {}
        for who, info in itertools.product(('committer', 'author'),
                                           ('name', 'email')):
            argname = '%s_%s' % (who, info)
            envname = 'GIT_%s_%s' % (who.upper(), info.upper())
            if argname in kwargs:
                env[envname] = kwargs[argname]
        for who in ('committer', 'author'):
            argname = '%s_date' % who
            envname = 'GIT_%s_DATE' % who.upper()
            if argname in kwargs:
                env[envname] = kwargs[argname].isoformat()
        return self._runcmd(['git', 'commit-tree', tree,
                             '-p', parent, '-m', message],
                            env=env).strip()

    @staticmethod
    def _check_is_sha1(string):
        if not morphlib.git.is_valid_sha1(string):
            raise ExpectedSha1Error(string)

    def _update_ref(self, ref_args, message):
        args = ['git', 'update-ref']
        # No test coverage, since while this functionality is useful,
        # morph does not need an API for inspecting the reflog, so
        # it existing purely to test ref updates is a tad overkill.
        if message is not None: # pragma: no cover
            args.extend(('-m', message))
        args.extend(ref_args)
        self._runcmd(args)

    def add_ref(self, ref, sha1, message=None):
        '''Create a ref called `ref` in the repository pointing to `sha1`.

        `message` is a string to add to the reflog about this change
        `ref` must not already exist, if it does, use `update_ref`
        `sha1` must be a 40 character hexadecimal string representing
        the SHA1 of the commit or tag this ref will point to, this is
        the result of the commit_tree or resolve_ref_to_commit methods.

        '''
        self._check_is_sha1(sha1)
        # 40 '0' characters is code for no previous value
        # this ensures it will fail if the branch already exists
        try:
            return self._update_ref((ref, sha1, '0' * 40), message)
        except Exception, e:
            raise RefAddError(self, ref, sha1, e)

    def update_ref(self, ref, sha1, old_sha1, message=None):
        '''Change the commit the ref `ref` points to, to `sha1`.

        `message` is a string to add to the reflog about this change
        `sha1` and `old_sha` must be 40 character hexadecimal strings
        representing the SHA1 of the commit or tag this ref will point
        to and currently points to respectively. This is the result of
        the commit_tree or resolve_ref_to_commit methods.
        `ref` must exist, and point to `old_sha1`.
        This is to avoid unexpected results when multiple processes
        attempt to change refs.

        '''
        self._check_is_sha1(sha1)
        self._check_is_sha1(old_sha1)
        try:
            return self._update_ref((ref, sha1, old_sha1), message)
        except Exception, e:
            raise RefUpdateError(self, ref, old_sha1, sha1, e)

    def delete_ref(self, ref, old_sha1, message=None):
        '''Remove the ref `ref`.

        `message` is a string to add to the reflog about this change
        `old_sha1` must be a 40 character hexadecimal string representing
        the SHA1 of the commit or tag this ref will point to, this is
        the result of the commit_tree or resolve_ref_to_commit methods.
        `ref` must exist, and point to `old_sha1`.
        This is to avoid unexpected results when multiple processes
        attempt to change refs.

        '''
        self._check_is_sha1(old_sha1)
        try:
            return self._update_ref(('-d', ref, old_sha1), message)
        except Exception, e:
            raise RefDeleteError(self, ref, old_sha1, e)


def init(dirname):
    '''Initialise a new git repository.'''

    gd = GitDirectory(dirname)
    gd._runcmd(['git', 'init'])
    return gd


def clone_from_cached_repo(cached_repo, dirname, ref): # pragma: no cover
    '''Clone a CachedRepo into the desired directory.

    The given ref is checked out (or git's default branch is checked out
    if ref is None).

    '''

    cached_repo.clone_checkout(ref, dirname)
    return GitDirectory(dirname)


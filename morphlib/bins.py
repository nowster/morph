# Copyright (C) 2011-2013  Codethink Limited
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


'''Functions for dealing with Baserock binaries.

Binaries are chunks, strata, and system images.

'''


import cliapp
import logging
import os
import re
import errno
import stat
import shutil
import tarfile

import morphlib

from morphlib.extractedtarball import ExtractedTarball
from morphlib.mountableimage import MountableImage


# Work around http://bugs.python.org/issue16477
def safe_makefile(self, tarinfo, targetpath):
    '''Create a file, closing correctly in case of exception'''

    source = self.extractfile(tarinfo)
    try:
        with open(targetpath, "wb") as target:
            shutil.copyfileobj(source, target)
    finally:
        source.close()

tarfile.TarFile.makefile = safe_makefile


def chunk_filenames(rootdir, regexps, dump_memory_profile=None):

    '''Return the filenames for a chunk from the contents of a directory.

    Only files and directories that match at least one of the regular
    expressions are accepted. The regular expressions are implicitly
    anchored to the beginning of the string, but not the end. The
    filenames are relative to rootdir.

    '''

    dump_memory_profile = dump_memory_profile or (lambda msg: None)

    def matches(filename):
        return any(x.match(filename) for x in compiled)

    def names_to_root(filename):
        yield filename
        while filename != rootdir:
            filename = os.path.dirname(filename)
            yield filename

    logging.debug('regexps: %s' % repr(regexps))
    compiled = [re.compile(x) for x in regexps]
    include = set()
    for dirname, subdirs, basenames in os.walk(rootdir):
        subdirpaths = [os.path.join(dirname, x) for x in subdirs]
        subdirsymlinks = [x for x in subdirpaths if os.path.islink(x)]
        filenames = [os.path.join(dirname, x) for x in basenames]
        for filename in [dirname] + subdirsymlinks + filenames:
            if matches(os.path.relpath(filename, rootdir)):
                for name in names_to_root(filename):
                    if name not in include:
                        logging.debug('regexp match: %s' % name)
                        include.add(name)
            else:
                logging.debug('regexp MISMATCH: %s' % filename)
    dump_memory_profile('after walking')

    return sorted(include)  # get dirs before contents


def chunk_contents(rootdir, regexps):
    ''' Return the list of files in a chunk, with the rootdir
        stripped off.
    
    '''
    
    filenames = chunk_filenames(rootdir, regexps)
    # The first entry is the rootdir directory, which we don't need
    filenames.pop(0)
    contents = [str[len(rootdir):] for str in filenames]
    return contents


def create_chunk(rootdir, f, regexps, dump_memory_profile=None):
    '''Create a chunk from the contents of a directory.
    
    ``f`` is an open file handle, to which the tar file is written.

    '''

    dump_memory_profile = dump_memory_profile or (lambda msg: None)

    # This timestamp is used to normalize the mtime for every file in
    # chunk artifact. This is useful to avoid problems from smallish
    # clock skew. It needs to be recent enough, however, that GNU tar
    # does not complain about an implausibly old timestamp.
    normalized_timestamp = 683074800

    include = chunk_filenames(rootdir, regexps, dump_memory_profile)
    logging.debug('Creating chunk file %s from %s with regexps %s' %
                  (getattr(f, 'name', 'UNNAMED'), rootdir, regexps))
    dump_memory_profile('at beginning of create_chunk')
    
    tar = tarfile.open(fileobj=f, mode='w')
    for filename in include:
        # Normalize mtime for everything.
        tarinfo = tar.gettarinfo(filename,
                                 arcname=os.path.relpath(filename, rootdir))
        tarinfo.ctime = normalized_timestamp
        tarinfo.mtime = normalized_timestamp
        if tarinfo.isreg():
            with open(filename, 'rb') as f:
                tar.addfile(tarinfo, fileobj=f)
        else:
            tar.addfile(tarinfo)
    tar.close()

    include.remove(rootdir)
    for filename in reversed(include):
        if os.path.isdir(filename) and not os.path.islink(filename):
            if not os.listdir(filename):
                os.rmdir(filename)
        else:
            os.remove(filename)
    dump_memory_profile('after removing in create_chunks')


def unpack_binary_from_file(f, dirname):  # pragma: no cover
    '''Unpack a binary into a directory.

    The directory must exist already.

    '''

    # This is evil, but necessary. For some reason Python's system
    # call wrappers (os.mknod and such) do not (always?) set the
    # filename attribute of the OSError exception they raise. We
    # fix that by monkey patching the tf instance with wrappers
    # for the relevant methods to add things. The wrapper further
    # ignores EEXIST errors, since we do not (currently!) care about
    # overwriting files.

    def follow_symlink(path):  # pragma: no cover
        try:
            return os.stat(path)
        except OSError:
            return None

    def prepare_extract(tarinfo, targetpath):  # pragma: no cover
        '''Prepare to extract a tar file member onto targetpath?

        If the target already exist, and we can live with it or
        remove it, we do so. Otherwise, raise an error.

        It's OK to extract if:

        * the target does not exist
        * the member is a directory a directory and the
          target is a directory or a symlink to a directory
          (just extract, no need to remove)
        * the member is not a directory, and the target is not a directory
          or a symlink to a directory (remove target, then extract)

        '''

        try:
            existing = os.lstat(targetpath)
        except OSError:
            return True  # target does not exist

        if tarinfo.isdir():
            if stat.S_ISDIR(existing.st_mode):
                return True
            elif stat.S_ISLNK(existing.st_mode):
                st = follow_symlink(targetpath)
                return st and stat.S_ISDIR(st.st_mode)
        else:
            if stat.S_ISDIR(existing.st_mode):
                return False
            elif stat.S_ISLNK(existing.st_mode):
                st = follow_symlink(targetpath)
                if st and not stat.S_ISDIR(st.st_mode):
                    os.remove(targetpath)
                    return True
            else:
                os.remove(targetpath)
                return True
        return False

    def monkey_patcher(real):
        def make_something(tarinfo, targetpath):  # pragma: no cover
            prepare_extract(tarinfo, targetpath)
            try:
                ret = real(tarinfo, targetpath)
            except (IOError, OSError), e:
                if e.errno != errno.EEXIST:
                    if e.filename is None:
                        e.filename = targetpath
                    raise
            else:
                return ret
        return make_something

    tf = tarfile.open(fileobj=f, errorlevel=2)
    tf.makedir = monkey_patcher(tf.makedir)
    tf.makefile = monkey_patcher(tf.makefile)
    tf.makeunknown = monkey_patcher(tf.makeunknown)
    tf.makefifo = monkey_patcher(tf.makefifo)
    tf.makedev = monkey_patcher(tf.makedev)
    tf.makelink = monkey_patcher(tf.makelink)

    try:
        tf.extractall(path=dirname)
    finally:
        tf.close()


def unpack_binary(filename, dirname):
    with open(filename, "rb") as f:
        unpack_binary_from_file(f, dirname)


class ArtifactNotMountableError(cliapp.AppException): # pragma: no cover

    def __init__(self, filename):
        cliapp.AppException.__init__(
                self, 'Artifact %s cannot be extracted or mounted' % filename)


def call_in_artifact_directory(app, filename, callback): # pragma: no cover
    '''Call a function in a directory the artifact is extracted/mounted in.'''

    try:
        with ExtractedTarball(app, filename) as dirname:
            callback(dirname)
    except tarfile.TarError:
        try:
            with MountableImage(app, filename) as dirname:
                callback(dirname)
        except (IOError, OSError):
            raise ArtifactNotMountableError(filename)

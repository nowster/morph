# Copyright (C) 2011-2012  Codethink Limited
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


import collections
import json
import logging
import os
import shutil
import time

import morphlib


def ldconfig(ex, rootdir):
    '''Run ldconfig for the filesystem below ``rootdir``.

    Essentially, ``rootdir`` specifies the root of a new system.
    Only directories below it are considered.

    ``etc/ld.so.conf`` below ``rootdir`` is assumed to exist and
    be populated by the right directories, and should assume
    the root directory is ``rootdir``. Example: if ``rootdir``
    is ``/tmp/foo``, then ``/tmp/foo/etc/ld.so.conf`` should
    contain ``/lib``, not ``/tmp/foo/lib``.
    
    The ldconfig found via ``$PATH`` is used, not the one in ``rootdir``,
    since in bootstrap mode that might not yet exist, the various 
    implementations should be compatible enough.

    '''

    conf = os.path.join(rootdir, 'etc', 'ld.so.conf')
    if os.path.exists(conf):
        logging.debug('Running ldconfig for %s' % rootdir)
        cache = os.path.join(rootdir, 'etc', 'ld.so.cache')
        old_path = ex.env['PATH']
        ex.env['PATH'] = '%s:/sbin:/usr/sbin:/usr/local/sbin' % old_path
        ex.runv(['ldconfig', '-r', rootdir])
        ex.env['PATH'] = old_path
    else:
        logging.debug('No %s, not running ldconfig' % conf)


class BlobBuilder(object):

    def __init__(self, app, blob):
        self.app = app
        self.blob = blob
        
        # The following MUST get set by the caller.
        self.builddir = None
        self.destdir = None
        self.staging = None
        self.settings = None
        self.real_msg = None
        self.cache_prefix = None
        self.tempdir = None
        self.logfile = None
        self.stage_items = []
        self.dump_memory_profile = lambda msg: None

        # Stopwatch to measure build times
        self.build_watch = morphlib.stopwatch.Stopwatch()

    def msg(self, text):
        self.real_msg(text)
        if self.logfile and not self.logfile.closed:
            self.logfile.write('%s\n' % text)

    def builds(self):
        ret = {}
        for chunk_name in self.blob.chunks:
            ret[chunk_name] = self.filename(chunk_name)
        return ret

    def build(self):
        self.prepare_logfile()

        # create the staging area on demand
        if not os.path.exists(self.staging):
            os.mkdir(self.staging)

        # record all items built in the process
        built_items = []

        # get a list of all the items we have to build for this blob
        builds = self.builds()

        # if not all build items are in the cache, rebuild the blob
        if not all(os.path.isfile(builds[name]) for name in builds):
            with self.build_watch('overall-build'):
                self.do_build()

        # check again, fail if not all build items were actually built
        if not all(os.path.isfile(builds[name]) for name in builds):
            raise Exception('Not all builds results expected from %s were '
                            'actually built' % self.blob)

        # install all build items to the staging area
        for name, filename in builds.items():
            self.msg('Fetching cached %s %s from %s' %
                     (self.blob.morph.kind, name, filename))
            self.install_chunk(name, filename)
            self.dump_memory_profile('after installing chunk')

            built_items.append((name, filename))

        # store the logged build times in the cache
        self.save_build_times()

        # store the log file in the cache
        self.save_logfile()

        return built_items

    def filename(self, name):
        return '%s.%s.%s' % (self.cache_prefix,
                             self.blob.morph.kind,
                             name)

    def install_chunk(self, chunk_name, chunk_filename):
        if self.blob.morph.kind != 'chunk':
            return
        if self.settings['bootstrap']:
            self.msg('Unpacking item %s onto system' % chunk_name)
            ex = morphlib.execute.Execute('/', self.msg)
            morphlib.bins.unpack_binary(chunk_filename, '/', ex)
            ldconfig(ex, '/')
        else:
            self.msg('Unpacking chunk %s into staging' % chunk_name)
            ex = morphlib.execute.Execute('/', self.msg)
            morphlib.bins.unpack_binary(chunk_filename, self.staging, ex)
            ldconfig(ex, self.staging)

    def prepare_binary_metadata(self, blob_name, **kwargs):
        '''Add metadata to a binary about to be built.'''

        self.msg('Adding metadata to %s' % blob_name)
        meta = {
            'name': blob_name,
            'kind': self.blob.morph.kind,
            'description': self.blob.morph.description,
        }
        for key, value in kwargs.iteritems():
            meta[key] = value
        
        dirname = os.path.join(self.destdir, 'baserock')
        filename = os.path.join(dirname, '%s.meta' % blob_name)
        if not os.path.exists(dirname):
            os.mkdir(dirname)
            
        with open(filename, 'w') as f:
            json.dump(meta, f, indent=4)
            f.write('\n')

    def write_cache_metadata(self, meta):
        self.msg('Writing metadata to the cache')
        filename = '%s.meta' % self.cache_prefix
        with open(filename, 'w') as f:
            json.dump(meta, f, indent=4)
            f.write('\n')

    def save_build_times(self):
        meta = {
            'build-times': {}
        }
        for stage in self.build_watch.ticks.iterkeys():
            meta['build-times'][stage] = {
                'start': '%s' % self.build_watch.start_time(stage),
                'stop': '%s' % self.build_watch.stop_time(stage),
                'delta': '%.4f' % self.build_watch.start_stop_seconds(stage)
            }
        self.write_cache_metadata(meta)

    def prepare_logfile(self):
        filename = self.tempdir.join('%s.log' % self.blob.morph.name)
        self.logfile = open(filename, 'w+', 0)

    def save_logfile(self):
        self.logfile.close()
        filename = '%s.log' % self.cache_prefix
        self.msg('Saving build log to %s' % filename)
        shutil.copyfile(self.logfile.name, filename)


class ChunkBuilder(BlobBuilder):

    build_system = {
        'dummy': {
            'configure-commands': [
                'echo dummy configure',
            ],
            'build-commands': [
                'echo dummy build',
            ],
            'test-commands': [
                'echo dummy test',
            ],
            'install-commands': [
                'echo dummy install',
            ],
        },
        'autotools': {
            'configure-commands': [
                'if [ -e autogen.sh ]; then ./autogen.sh; ' +
                'elif [ ! -e ./configure ]; then autoreconf -ivf; fi',
                './configure --prefix=/usr',
            ],
            'build-commands': [
                'make',
            ],
            'test-commands': [
            ],
            'install-commands': [
                'make DESTDIR="$DESTDIR" install',
            ],
        },
    }

    def do_build(self):
        self.msg('Creating build tree at %s' % self.builddir)

        self.ex = morphlib.execute.Execute(self.builddir, self.msg)
        self.setup_env()

        self.prepare_build_directory()

        os.mkdir(self.destdir)
        self.build_with_system_or_commands()
        self.dump_memory_profile('after building chunk')

        chunks = self.create_chunks()
        self.dump_memory_profile('after creating build chunks')
        return chunks
        
    def setup_env(self):
        path = self.ex.env['PATH']
        tools = self.ex.env.get('BOOTSTRAP_TOOLS')
        distcc_hosts = self.ex.env.get('DISTCC_HOSTS')

        # copy a set of white-listed variables from the original env
        copied_vars = dict.fromkeys([
            'TMPDIR',
            'LD_PRELOAD',
            'LD_LIBRARY_PATH',
            'FAKEROOTKEY',
            'FAKED_MODE',
            'FAKEROOT_FD_BASE',
        ])
        for name in copied_vars:
            copied_vars[name] = self.ex.env.get(name, None)

        self.ex.env.clear()
        
        # apply the copied variables to the clean env
        for name in copied_vars:
            if copied_vars[name] is not None:
                self.ex.env[name] = copied_vars[name]

        self.ex.env['TERM'] = 'dumb'
        self.ex.env['SHELL'] = '/bin/sh'
        self.ex.env['USER'] = \
            self.ex.env['USERNAME'] = \
            self.ex.env['LOGNAME'] = 'tomjon'
        self.ex.env['LC_ALL'] = 'C'
        self.ex.env['HOME'] = os.path.join(self.tempdir.dirname)

        if self.settings['keep-path'] or self.settings['bootstrap']:
            self.ex.env['PATH'] = path
        else:
            bindirs = ['bin']
            path = ':'.join(os.path.join(self.tempdir.dirname, x) 
                                         for x in bindirs)
            self.ex.env['PATH'] = path

        self.ex.env['WORKAREA'] = self.tempdir.dirname
        self.ex.env['DESTDIR'] = self.destdir + '/'
        self.ex.env['TOOLCHAIN_TARGET'] = \
            '%s-baserock-linux-gnu' % os.uname()[4]
        self.ex.env['BOOTSTRAP'] = \
            'true' if self.settings['bootstrap'] else 'false'
        if tools is not None:
            self.ex.env['BOOTSTRAP_TOOLS'] = tools
        if distcc_hosts is not None:
            self.ex.env['DISTCC_HOSTS'] = distcc_hosts

        if self.blob.morph.max_jobs:
            max_jobs = int(self.blob.morph.max_jobs)
            logging.debug('max_jobs from morph: %s' % max_jobs)
        elif self.settings['max-jobs']:
            max_jobs = self.settings['max-jobs']
            logging.debug('max_jobs from settings: %s' % max_jobs)
        else:
            max_jobs = morphlib.util.make_concurrency()
            logging.debug('max_jobs from cpu count: %s' % max_jobs)
        self.ex.env['MAKEFLAGS'] = '-j%d' % max_jobs

        if not self.settings['no-ccache']:
            self.ex.env['PATH'] = ('/usr/lib/ccache:%s' % 
                                    self.ex.env['PATH'])
            self.ex.env['CCACHE_BASEDIR'] = self.tempdir.dirname
            if not self.settings['no-distcc']:
                self.ex.env['CCACHE_PREFIX'] = 'distcc'

        logging.debug('Environment for building chunk:')
        for key in sorted(self.ex.env):
            logging.debug('  %s=%s' % (key, self.ex.env[key]))

    def prepare_build_directory(self):
        os.mkdir(self.builddir)

        def extract_treeish(treeish, destdir):
            self.msg('Extracting %s into %s' %
                     (treeish.repo, self.builddir))

            morphlib.git.copy_repository(treeish, destdir, self.msg)
            morphlib.git.checkout_ref(destdir, treeish.ref, self.msg)
            self.set_mtime_recursively(destdir)

            for submodule in treeish.submodules:
                directory = os.path.join(destdir, submodule.path)
                extract_treeish(submodule.treeish, directory)

                # we need to do this to keep any "git submodule" commands
                # from accessing the internet. instead, we redirect them
                # to the locally cached submodule repo
                morphlib.git.set_submodule_url(destdir, submodule.name,
                                               submodule.treeish.repo,
                                               self.msg)

        extract_treeish(self.blob.morph.treeish, self.builddir)

    def set_mtime_recursively(self, root):
        '''Set the mtime for every file in a directory tree to the same.
        
        We do this because git checkout does not set the mtime to anything,
        and some projects (binutils, gperf for example) include formatted
        documentation and try to randomly build things or not because of
        the timestamps. This should help us get more reliable  builds.
        
        '''
        
        now = time.time()
        for dirname, subdirs, basenames in os.walk(root, topdown=False):
            for basename in basenames:
                pathname = os.path.join(dirname, basename)
                os.utime(pathname, (now, now))
            os.utime(dirname, (now, now))

    def build_with_system_or_commands(self):
        '''Run explicit commands or commands from build system.
        
        Use explicit commands, if given, and build system commands if one
        has been specified.
        
        '''

        bs_name = self.blob.morph.build_system
        if bs_name:
            bs = self.build_system[bs_name]
        else:
            bs = {}

        def run_them(runner, what):
            key = '%s-commands' % what
            attr = '%s_commands' % what
            cmds = bs.get(key, [])
            cmds = getattr(self.blob.morph, attr, []) or cmds
            runner(what, cmds)

        run_them(self.run_sequentially, 'configure')
        run_them(self.run_in_parallel, 'build')
        run_them(self.run_sequentially, 'test')
        run_them(self.run_sequentially, 'install')

    def run_in_parallel(self, what, commands):
        self.msg('commands: %s' % what)
        with self.build_watch(what):
            self.run_commands(commands)

    def run_sequentially(self, what, commands):
        self.msg ('commands: %s' % what)
        with self.build_watch(what):
            flags = self.ex.env['MAKEFLAGS']
            self.ex.env['MAKEFLAGS'] = '-j1'
            logging.debug('Setting MAKEFLAGS=%s' % self.ex.env['MAKEFLAGS'])
            self.run_commands(commands)
            self.ex.env['MAKEFLAGS'] = flags
            logging.debug('Restore MAKEFLAGS=%s' % self.ex.env['MAKEFLAGS'])

    def run_commands(self, commands):
        if self.settings['staging-chroot']:
            ex = morphlib.execute.Execute(self.staging, self.msg)
            ex.env.clear()
            for key in self.ex.env:
                ex.env[key] = self.ex.env[key]
            assert self.builddir.startswith(self.staging + '/')
            assert self.destdir.startswith(self.staging + '/')
            builddir = self.builddir[len(self.staging):]
            destdir = self.destdir[len(self.staging):]
            for cmd in commands:
                old_destdir = ex.env.get('DESTDIR', None)
                ex.env['DESTDIR'] = destdir
                ex.runv(['/usr/sbin/chroot', self.staging, 'sh', '-c',
                         'cd "$1" && shift && eval "$@"', '--', builddir, cmd])
                if old_destdir is None:
                    del ex.env['DESTDIR']
                else:
                    ex.env['DESTDIR'] = old_destdir
        else:
            self.ex.run(commands)

    def create_chunks(self):
        chunks = []
        with self.build_watch('create-chunks'):
            for chunk_name in self.blob.chunks:
                self.msg('Creating chunk %s' % chunk_name)
                self.prepare_binary_metadata(chunk_name)
                patterns = self.blob.chunks[chunk_name]
                patterns += [r'baserock/%s\.' % chunk_name]
                filename = self.filename(chunk_name)
                self.msg('Creating binary for %s' % chunk_name)
                morphlib.bins.create_chunk(self.destdir, filename, patterns,
                                           self.ex, self.dump_memory_profile)
                chunks.append((chunk_name, filename))

        files = os.listdir(self.destdir)
        if files:
            raise Exception('DESTDIR %s is not empty: %s' %
                                (self.destdir, files))
        return chunks


class StratumBuilder(BlobBuilder):
    
    def builds(self):
        filename = self.filename(self.blob.morph.name)
        return { self.blob.morph.name: filename }

    def do_build(self):
        os.mkdir(self.destdir)
        ex = morphlib.execute.Execute(self.destdir, self.msg)
        with self.build_watch('unpack-chunks'):
            for chunk_name, filename in self.stage_items:
                self.msg('Unpacking chunk %s' % chunk_name)
                morphlib.bins.unpack_binary(filename, self.destdir, ex)
        with self.build_watch('create-binary'):
            self.prepare_binary_metadata(self.blob.morph.name)
            self.msg('Creating binary for %s' % self.blob.morph.name)
            filename = self.filename(self.blob.morph.name)
            morphlib.bins.create_stratum(self.destdir, filename, ex)
        return { self.blob.morph.name: filename }


class SystemBuilder(BlobBuilder):

    def needs_built(self):
        for stratum_name in self.morph.strata:
            yield self.repo, self.ref, stratum_name, [stratum_name]

    def do_build(self):
        self.ex = morphlib.execute.Execute(self.tempdir.dirname, self.msg)
        
        # Create image.
        with self.build_watch('create-image'):
            image_name = self.tempdir.join('%s.img' % self.blob.morph.name)
            self.ex.runv(['qemu-img', 'create', '-f', 'raw', image_name,
                          self.blob.morph.disk_size])

        # Partition it.
        with self.build_watch('partition-image'):
            self.ex.runv(['parted', '-s', image_name, 'mklabel', 'msdos'])
            self.ex.runv(['parted', '-s', image_name, 'mkpart', 'primary', 
                          '0%', '100%'])
            self.ex.runv(['parted', '-s', image_name, 'set', '1', 'boot', 'on'])

        # Install first stage boot loader into MBR.
        with self.build_watch('install-mbr'):
            self.ex.runv(['install-mbr', image_name])

        # Setup device mapper to access the partition.
        with self.build_watch('setup-device-mapper'):
            out = self.ex.runv(['kpartx', '-av', image_name])
            devices = [line.split()[2]
                       for line in out.splitlines()
                       if line.startswith('add map ')]
            partition = '/dev/mapper/%s' % devices[0]

        mount_point = None
        try:
            # Create filesystem.
            with self.build_watch('create-filesystem'):
                self.ex.runv(['mkfs', '-t', 'ext3', partition])

            # Mount it.
            with self.build_watch('mount-filesystem'):
                mount_point = self.tempdir.join('mnt')
                os.mkdir(mount_point)
                self.ex.runv(['mount', partition, mount_point])

            # Unpack all strata into filesystem.
            # Also, run ldconfig.
            with self.build_watch('unpack-strata'):
                for name, filename in self.stage_items:
                    self.msg('unpack %s from %s' % (name, filename))
                    self.ex.runv(['tar', '-C', mount_point, '-xf', filename])
                ldconfig(self.ex, mount_point)

            # Create fstab.
            with self.build_watch('create-fstab'):
                fstab = self.tempdir.join('mnt/etc/fstab')
                if not os.path.exists(os.path.dirname(fstab)):
                    os.makedirs(os.path.dirname(fstab))
                # sorry about the hack, I wish I knew a better way
                self.ex.runv(['tee', fstab], feed_stdin='''
proc      /proc proc  defaults          0 0
sysfs     /sys  sysfs defaults          0 0
/dev/sda1 /     ext4  errors=remount-ro 0 1
''', stdout=open(os.devnull,'w'))

            # Install extlinux bootloader.
            with self.build_watch('install-bootloader'):
                conf = os.path.join(mount_point, 'extlinux.conf')
                logging.debug('configure extlinux %s' % conf)
                self.ex.runv(['tee', conf], feed_stdin='''
default linux
timeout 1

label linux
kernel /vmlinuz
append root=/dev/sda1 init=/sbin/init quiet rw
''', stdout=open(os.devnull, 'w'))

                self.ex.runv(['extlinux', '--install', mount_point])
                
                # Weird hack that makes extlinux work. 
                # FIXME: There is a bug somewhere.
                self.ex.runv(['sync'])
                import time; time.sleep(2)

            # Unmount.
            with self.build_watch('unmount-filesystem'):
                self.ex.runv(['umount', mount_point])
        except BaseException:
            # Unmount.
            if mount_point is not None:
                try:
                    self.ex.runv(['umount', mount_point])
                except Exception:
                    pass

            # Undo device mapping.
            try:
                self.ex.runv(['kpartx', '-d', image_name])
            except Exception:
                pass
            raise

        # Undo device mapping.
        with self.build_watch('undo-device-mapper'):
            self.ex.runv(['kpartx', '-d', image_name])

        # Move image file to cache.
        with self.build_watch('cache-image'):
            filename = self.filename(self.blob.morph.name)
            self.ex.runv(['mv', image_name, filename])

        return { self.blob.morph.name: filename }

class Builder(object):

    '''Build binary objects for Baserock.
    
    The objects may be chunks or strata.'''
    
    def __init__(self, tempdir, app, morph_loader, source_manager):
        self.tempdir = tempdir
        self.app = app
        self.real_msg = app.msg
        self.settings = app.settings
        self.dump_memory_profile = app.dump_memory_profile
        self.cachedir = morphlib.cachedir.CacheDir(self.settings['cachedir'])
        self.morph_loader = morph_loader
        self.source_manager = source_manager
        self.indent = 0

    def msg(self, text):
        spaces = '  ' * self.indent
        self.real_msg('%s%s' % (spaces, text))

    def indent_more(self):
        self.indent += 1
    
    def indent_less(self):
        self.indent -= 1

    def build(self, blobs, build_order):
        '''Build a list of groups of morphologies. Items in a group
           can be built in parallel.'''

        self.indent_more()

        # first pass: create builders for all blobs
        builders = {}
        for group in build_order:
            for blob in group:
                builder = self.create_blob_builder(blob)
                builders[blob] = builder

        # second pass: build group by group, item after item
        ret = []
        for group in build_order:
            for blob in group:
                self.msg('Building %s' % blob)
                self.indent_more()

                ## TODO this needs to be done recursively
                ## make sure all dependencies are in the staging area
                #for dependency in blob.dependencies:
                #    depbuilder = builders[dependency]
                #    depbuilder.stage()

                ## TODO this needs the set of recursively collected 
                ## dependencies
                ## make sure all non-dependencies are not staged
                #for nondependency in (blobs - blob.dependencies):
                #    depbuilder = builders[nondependency]
                #    depbuilder.unstage()

                built_items = builders[blob].build()
                
                for parent in blob.parents:
                    for item, filename in built_items:
                        self.msg('Marking %s to be staged for %s' %
                                 (item, parent))

                    parent_builder = builders[parent]
                    parent_builder.stage_items += built_items

                self.indent_less()

        self.indent_less()

        return ret

    def build_single(self, blob, blobs, build_order):
        self.indent_more()

        # first pass: create builders for all blobs
        builders = {}
        for group in build_order:
            for b in group:
                builder = self.create_blob_builder(b)
                builders[b] = builder

        # second pass: mark all dependencies for staging
        queue = collections.deque()
        visited = set()
        for dependency in blob.dependencies:
            queue.append(dependency)
            visited.add(dependency)
        while len(queue) > 0:
            dependency = queue.popleft()
            built_items = builders[dependency].builds()
            for name, filename in built_items.items():
                self.msg('Marking %s to be staged for %s' % (name, blob))
                builders[blob].stage_items.append((name, filename))
            for dep in dependency.dependencies:
                if (dependency.morph.kind == 'stratum' and
                        not dep.morph.kind == 'chunk'):
                    if dep not in visited:
                        queue.append(dep)
                        visited.add(dep)

        # build the single blob now
        ret = []
        ret.append(builders[blob].build())
        self.indent_less()
        return ret

    def create_blob_builder(self, blob):
        if isinstance(blob, morphlib.blobs.Stratum):
            builder = StratumBuilder(self.app, blob)
        elif isinstance(blob, morphlib.blobs.Chunk):
            builder = ChunkBuilder(self.app, blob)
        elif isinstance(blob, morphlib.blobs.System):
            builder = SystemBuilder(self.app, blob)
        else:
            raise TypeError('Blob %s has unknown type %s' %
                            (str(blob), type(blob)))

        cache_id = self.get_cache_id(blob)
        logging.debug('cache id: %s' % repr(cache_id))
        self.dump_memory_profile('after computing cache id')

        builder.staging = self.tempdir.join('staging')
        s = builder.staging
        builder.builddir = os.path.join(s, '%s.build' % blob.morph.name)
        builder.destdir = os.path.join(s, '%s.inst' % blob.morph.name)
        builder.settings = self.settings
        builder.real_msg = self.msg
        builder.cache_prefix = self.cachedir.name(cache_id)
        builder.tempdir = self.tempdir
        builder.dump_memory_profile = self.dump_memory_profile
        
        return builder

    def get_cache_id(self, blob):
        logging.debug('get_cache_id(%s)' % blob)

        if blob.morph.kind == 'chunk':
            kids = []
        elif blob.morph.kind == 'stratum':
            kids = []
            for source in blob.morph.sources:
                repo = source['repo']
                ref = source['ref']
                treeish = self.source_manager.get_treeish(repo, ref)
                filename = (source['morph']
                            if 'morph' in source
                            else source['name'])
                filename = '%s.morph' % filename
                morph = self.morph_loader.load(treeish, filename)
                chunk = morphlib.blobs.Blob.create_blob(morph)
                cache_id = self.get_cache_id(chunk)
                kids.append(cache_id)
        elif blob.morph.kind == 'system':
            kids = []
            for stratum_name in blob.morph.strata:
                filename = '%s.morph' % stratum_name
                morph = self.morph_loader.load(blob.morph.treeish, filename)
                stratum = morphlib.blobs.Blob.create_blob(morph)
                cache_id = self.get_cache_id(stratum)
                kids.append(cache_id)
        else:
            raise NotImplementedError('unknown morph kind %s' %
                                      blob.morph.kind)

        dict_key = {
            'name': blob.morph.name,
            'arch': morphlib.util.arch(),
            'ref': blob.morph.treeish.sha1,
            'kids': ''.join(self.cachedir.key(k) for k in kids),
        }
        return dict_key


# Copyright (C) 2012, 2013, 2014  Codethink Limited
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


class Artifact(object):

    '''Represent a build result generated from a source.

    Has the following properties:

    * ``source`` -- the source from which the artifact is built
    * ``name`` -- the name of the artifact
    * ``cache_key`` -- a cache key to uniquely identify the artifact
    * ``cache_id`` -- a dict describing the components of the cache key
    * ``dependencies`` -- list of Artifacts that need to be built beforehand
    * ``dependents`` -- list of Artifacts that need this Artifact to be built
    * ``metadata_version`` -- When the format of the artifact metadata
                              changes, this version number is raised causing 
                              any existing cached artifacts to be invalidated

    The ``dependencies`` and ``dependents`` lists MUST be modified by
    the ``add_dependencies`` and ``add_dependent`` methods only.

    '''

    def __init__(self, source, name):
        self.source = source
        self.name = name
        self.cache_id = None
        self.cache_key = None
        self.dependencies = []
        self.dependents = []
        self.metadata_version = 1

    def add_dependency(self, artifact):
        '''Add ``artifact`` to the dependency list.'''
        if artifact not in self.dependencies:
            self.dependencies.append(artifact)
            artifact.dependents.append(self)

    def depends_on(self, artifact):
        '''Do we depend on ``artifact``?'''
        return artifact in self.dependencies

    def basename(self):  # pragma: no cover
        return '%s.%s.%s' % (self.cache_key,
                             str(self.source.morphology['kind']),
                             str(self.name))

    def metadata_basename(self, metadata_name):  # pragma: no cover
        return '%s.%s.%s.%s' % (self.cache_key,
                                str(self.source.morphology['kind']),
                                str(self.name),
                                metadata_name)

    def get_dependency_prefix_set(self):
        '''Collects all install prefixes of this artifact's build dependencies

           If any of the build dependencies of a chunk artifact are installed
           to non-standard prefixes, we need to add those prefixes to the
           PATH of the current artifact.

        '''
        result = set()
        for d in self.dependencies:
            if d.source.morphology['kind'] == 'chunk':
                result.add(d.source.prefix)
        return result

    def __str__(self):  # pragma: no cover
        return '%s|%s' % (self.source, self.name)

    def __repr__(self): # pragma: no cover
        return 'Artifact(%s)' % str(self)

    def walk(self): # pragma: no cover
        '''Return list of an artifact and its build dependencies.
        
        The artifacts are returned in depth-first order: an artifact
        is returned only after all of its dependencies.
        
        '''
        
        done = set()
        
        def depth_first(a):
            if a not in done:
                done.add(a)
                for dep in a.dependencies:
                    for ret in depth_first(dep):
                        yield ret
                yield a

        return list(depth_first(self))

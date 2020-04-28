# #
# Copyright 2009-2020 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
# #
"""
EasyBuild support for installing the Intel MPI library, implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Damian Alvarez (Forschungszentrum Juelich GmbH)
@author: Alex Domingo (Vrije Universiteit Brussel)
"""
import os
from distutils.version import LooseVersion

from easybuild.easyblocks.generic.intelbase import IntelBase, ACTIVATION_NAME_2012, LICENSE_FILE_NAME_2012
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import apply_regex_substitutions, change_dir, extract_file, mkdir, write_file
from easybuild.tools.run import run_cmd
from easybuild.tools.systemtools import get_shared_lib_ext


class EB_impi(IntelBase):
    """
    Support for installing Intel MPI library
    """
    @staticmethod
    def extra_options():
        extra_vars = {
            'libfabric_configopts': ['', 'Configure options for the provided libfabric', CUSTOM],
            'libfabric_rebuild': [True, 'Try to rebuild internal libfabric instead of using provided binary', CUSTOM],
            'ofi_internal': [True, 'Use internal shipped libfabric instead of external libfabric', CUSTOM],
            'set_mpi_wrappers_compiler': [False, 'Override default compiler used by MPI wrapper commands', CUSTOM],
            'set_mpi_wrapper_aliases_gcc': [False, 'Set compiler for mpigcc/mpigxx via aliases', CUSTOM],
            'set_mpi_wrapper_aliases_intel': [False, 'Set compiler for mpiicc/mpiicpc/mpiifort via aliases', CUSTOM],
            'set_mpi_wrappers_all': [False, 'Set (default) compiler for all MPI wrapper commands', CUSTOM],
        }
        return IntelBase.extra_options(extra_vars)

    def prepare_step(self, *args, **kwargs):
        if LooseVersion(self.version) >= LooseVersion('2017.2.174'):
            kwargs['requires_runtime_license'] = False
            super(EB_impi, self).prepare_step(*args, **kwargs)
        else:
            super(EB_impi, self).prepare_step(*args, **kwargs)

    def install_step(self):
        """
        Actual installation
        - create silent cfg file
        - execute command
        """
        impiver = LooseVersion(self.version)
        if impiver >= LooseVersion('4.0.1'):
            # impi starting from version 4.0.1.x uses standard installation procedure.

            silent_cfg_names_map = {}

            if impiver < LooseVersion('4.1.1'):
                # since impi v4.1.1, silent.cfg has been slightly changed to be 'more standard'
                silent_cfg_names_map.update({
                    'activation_name': ACTIVATION_NAME_2012,
                    'license_file_name': LICENSE_FILE_NAME_2012,
                })

            super(EB_impi, self).install_step(silent_cfg_names_map=silent_cfg_names_map)

            # impi v4.1.1 and v5.0.1 installers create impi/<version> subdir, so stuff needs to be moved afterwards
            if impiver == LooseVersion('4.1.1.036') or impiver >= LooseVersion('5.0.1.035'):
                super(EB_impi, self).move_after_install()
        else:
            # impi up until version 4.0.0.x uses custom installation procedure.
            silent = """[mpi]
INSTALLDIR=%(ins)s
LICENSEPATH=%(lic)s
INSTALLMODE=NONRPM
INSTALLUSER=NONROOT
UPDATE_LD_SO_CONF=NO
PROCEED_WITHOUT_PYTHON=yes
AUTOMOUNTED_CLUSTER=yes
EULA=accept
[mpi-rt]
INSTALLDIR=%(ins)s
LICENSEPATH=%(lic)s
INSTALLMODE=NONRPM
INSTALLUSER=NONROOT
UPDATE_LD_SO_CONF=NO
PROCEED_WITHOUT_PYTHON=yes
AUTOMOUNTED_CLUSTER=yes
EULA=accept

""" % {'lic': self.license_file, 'ins': self.installdir}

            # already in correct directory
            silentcfg = os.path.join(os.getcwd(), "silent.cfg")
            write_file(silentcfg, silent)
            self.log.debug("Contents of %s: %s", silentcfg, silent)

            tmpdir = os.path.join(os.getcwd(), self.version, 'mytmpdir')
            mkdir(tmpdir, parents=True)

            cmd = "./install.sh --tmp-dir=%s --silent=%s" % (tmpdir, silentcfg)
            run_cmd(cmd, log_all=True, simple=True)

        # recompile libfabric (if requested)
        # some Intel MPI versions (like 2019 update 6) no longer ship libfabric sources
        libfabric_path = os.path.join(self.installdir, 'libfabric')
        if impiver >= LooseVersion('2019') and self.cfg['libfabric_rebuild']:
            if self.cfg['ofi_internal']:
                libfabric_src_tgz_fn = 'src.tgz'
                if os.path.exists(os.path.join(libfabric_path, libfabric_src_tgz_fn)):
                    change_dir(libfabric_path)
                    extract_file(libfabric_src_tgz_fn, os.getcwd())
                    libfabric_installpath = os.path.join(self.installdir, 'intel64', 'libfabric')

                    make = 'make'
                    if self.cfg['parallel']:
                        make += ' -j %d' % self.cfg['parallel']

                    cmds = [
                        './configure --prefix=%s %s' % (libfabric_installpath, self.cfg['libfabric_configopts']),
                        make,
                        'make install'
                    ]
                    for cmd in cmds:
                        run_cmd(cmd, log_all=True, simple=True)
                else:
                    self.log.info("Rebuild of libfabric is requested, but %s does not exist, so skipping...",
                                  libfabric_src_tgz_fn)
            else:
                raise EasyBuildError("Rebuild of libfabric is requested, but ofi_internal is set to False.")

    def post_install_step(self):
        """Custom post install step for IMPI, fix broken env scripts after moving installed files."""
        super(EB_impi, self).post_install_step()

        impiver = LooseVersion(self.version)
        if impiver == LooseVersion('4.1.1.036') or impiver >= LooseVersion('5.0.1.035'):
            if impiver >= LooseVersion('2018.0.128'):
                script_paths = [os.path.join('intel64', 'bin')]
            else:
                script_paths = [os.path.join('intel64', 'bin'), os.path.join('mic', 'bin')]
            # fix broken env scripts after the move
            regex_subs = [(r"^setenv I_MPI_ROOT.*", r"setenv I_MPI_ROOT %s" % self.installdir)]
            for script in [os.path.join(script_path, 'mpivars.csh') for script_path in script_paths]:
                apply_regex_substitutions(os.path.join(self.installdir, script), regex_subs)
            regex_subs = [(r"^(\s*)I_MPI_ROOT=[^;\n]*", r"\1I_MPI_ROOT=%s" % self.installdir)]
            for script in [os.path.join(script_path, 'mpivars.sh') for script_path in script_paths]:
                apply_regex_substitutions(os.path.join(self.installdir, script), regex_subs)

            # fix 'prefix=' in compiler wrapper scripts after moving installation (see install_step)
            wrappers = ['mpif77', 'mpif90', 'mpigcc', 'mpigxx', 'mpiicc', 'mpiicpc', 'mpiifort']
            regex_subs = [(r"^prefix=.*", r"prefix=%s" % self.installdir)]
            for script_dir in script_paths:
                for wrapper in wrappers:
                    wrapper_path = os.path.join(self.installdir, script_dir, wrapper)
                    if os.path.exists(wrapper_path):
                        apply_regex_substitutions(wrapper_path, regex_subs)

    def sanity_check_step(self):
        """Custom sanity check paths for IMPI."""

        suff = "64"
        if self.cfg['m32']:
            suff = ""

        mpi_mods = ['mpi.mod']
        if LooseVersion(self.version) > LooseVersion('4.0'):
            mpi_mods.extend(["mpi_base.mod", "mpi_constants.mod", "mpi_sizeofs.mod"])

        if LooseVersion(self.version) >= LooseVersion('2019'):
            bin_dir = 'intel64/bin'
            include_dir = 'intel64/include'
            lib_dir = 'intel64/lib/release'
        else:
            bin_dir = 'bin%s' % suff
            include_dir = 'include%s' % suff
            lib_dir = 'lib%s' % suff
            mpi_mods.extend(["i_malloc.h"])

        custom_paths = {
            'files': ["%s/mpi%s" % (bin_dir, x) for x in ["icc", "icpc", "ifort"]] +
                    ["%s/mpi%s.h" % (include_dir, x) for x in ["cxx", "f", "", "o", "of"]] +
                    ["%s/%s" % (include_dir, x) for x in mpi_mods] +
                    ["%s/libmpi.%s" % (lib_dir, get_shared_lib_ext())] +
                    ["%s/libmpi.a" % lib_dir],
            'dirs': [],
        }

        # Add minimal test program to sanity checks
        try:
            fake_mod_data = self.load_fake_module()
        except EasyBuildError as err:
            self.log.debug("Loading fake module failed: %s" % err)

        impi_testsrc = os.path.join(self.installdir, 'test/test.c')
        impi_testexe = os.path.join(self.builddir, 'mpi_test')
        self.log.info("Building minimal MPI test program: %s", impi_testsrc)
        build_test = "mpiicc %s -o %s" % (impi_testsrc, impi_testexe)
        run_cmd(build_test, log_all=True, simple=True)

        self.clean_up_fake_module(fake_mod_data)  # unload build environment

        custom_commands = ['mpirun -n %s %s' % (self.cfg['parallel'], impi_testexe)]

        super(EB_impi, self).sanity_check_step(custom_paths=custom_paths, custom_commands=custom_commands)

    def make_module_req_guess(self):
        """
        A dictionary of possible directories to look for
        """
        if self.cfg['m32']:
            lib_dirs = ['lib', 'lib/ia32', 'ia32/lib']
            include_dirs = ['include']
            return {
                'PATH': ['bin', 'bin/ia32', 'ia32/bin'],
                'LD_LIBRARY_PATH': lib_dirs,
                'LIBRARY_PATH': lib_dirs,
                'MANPATH': ['man'],
                'CPATH': include_dirs,
                'MIC_LD_LIBRARY_PATH': ['mic/lib'],
            }
        else:
            guesses = {}
            if LooseVersion(self.version) >= LooseVersion('2019'):
                # Keep release_mt and release in front, to give them priority over the possible symlinks in intel64/lib.
                # IntelMPI 2019 changed the default library to be the non-mt version.
                lib_dirs = ['intel64/%s' % x for x in ['lib/release_mt', 'lib/release', 'lib']]
                include_dirs = ['intel64/include']
                path_dirs = ['intel64/bin']
                if self.cfg['ofi_internal']:
                    lib_dirs.append('intel64/libfabric/lib')
                    path_dirs.append('intel64/libfabric/bin')
                    guesses['FI_PROVIDER_PATH'] = ['intel64/libfabric/lib/prov']
            else:
                lib_dirs = ['lib/em64t', 'lib64']
                include_dirs = ['include64']
                path_dirs = ['bin/intel64', 'bin64']
                guesses['MIC_LD_LIBRARY_PATH'] = ['mic/lib']

            guesses.update({
                'PATH': path_dirs,
                'LD_LIBRARY_PATH': lib_dirs,
                'LIBRARY_PATH': lib_dirs,
                'MANPATH': ['man'],
                'CPATH': include_dirs,
            })

            return guesses

    def make_module_extra(self, *args, **kwargs):
        """Overwritten from Application to add extra txt"""
        txt = super(EB_impi, self).make_module_extra(*args, **kwargs)
        txt += self.module_generator.set_environment('I_MPI_ROOT', self.installdir)
        if self.cfg['set_mpi_wrappers_compiler'] or self.cfg['set_mpi_wrappers_all']:
            for var in ['CC', 'CXX', 'F77', 'F90', 'FC']:
                if var == 'FC':
                    # $FC isn't defined by EasyBuild framework, so use $F90 instead
                    src_var = 'F90'
                else:
                    src_var = var

                target_var = 'I_MPI_%s' % var

                val = os.getenv(src_var)
                if val:
                    txt += self.module_generator.set_environment(target_var, val)
                else:
                    raise EasyBuildError("Environment variable $%s not set, can't define $%s", src_var, target_var)

        if self.cfg['set_mpi_wrapper_aliases_gcc'] or self.cfg['set_mpi_wrappers_all']:
            # force mpigcc/mpigxx to use GCC compilers, as would be expected based on their name
            txt += self.module_generator.set_alias('mpigcc', 'mpigcc -cc=gcc')
            txt += self.module_generator.set_alias('mpigxx', 'mpigxx -cxx=g++')

        if self.cfg['set_mpi_wrapper_aliases_intel'] or self.cfg['set_mpi_wrappers_all']:
            # do the same for mpiicc/mpiipc/mpiifort to be consistent, even if they may not exist
            txt += self.module_generator.set_alias('mpiicc', 'mpiicc -cc=icc')
            txt += self.module_generator.set_alias('mpiicpc', 'mpiicpc -cxx=icpc')
            # -fc also works, but -f90 takes precedence
            txt += self.module_generator.set_alias('mpiifort', 'mpiifort -f90=ifort')

        return txt

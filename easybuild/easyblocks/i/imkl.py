# #
# Copyright 2009-2021 Ghent University
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
EasyBuild support for installing the Intel Math Kernel Library (MKL), implemented as an easyblock

@author: Stijn De Weirdt (Ghent University)
@author: Dries Verdegem (Ghent University)
@author: Kenneth Hoste (Ghent University)
@author: Pieter De Baets (Ghent University)
@author: Jens Timmerman (Ghent University)
@author: Ward Poelmans (Ghent University)
@author: Lumir Jasiok (IT4Innovations)
"""

import itertools
import os
import shutil
import tempfile
from distutils.version import LooseVersion

import easybuild.tools.environment as env
import easybuild.tools.toolchain as toolchain
from easybuild.easyblocks.generic.intelbase import IntelBase, ACTIVATION_NAME_2012, LICENSE_FILE_NAME_2012
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import apply_regex_substitutions, change_dir, remove_dir, write_file
from easybuild.tools.modules import get_software_root
from easybuild.tools.run import run_cmd
from easybuild.tools.systemtools import get_shared_lib_ext


class EB_imkl(IntelBase):
    """
    Class that can be used to install mkl
    - tested with 10.2.1.017
    -- will fail for all older versions (due to newer silent installer)
    """

    @staticmethod
    def extra_options():
        """Add easyconfig parameters custom to imkl (e.g. interfaces)."""
        extra_vars = {
            'interfaces': [True, "Indicates whether interfaces should be built", CUSTOM],
        }
        return IntelBase.extra_options(extra_vars)

    def __init__(self, *args, **kwargs):
        super(EB_imkl, self).__init__(*args, **kwargs)
        # make sure $MKLROOT isn't set, it's known to cause problems with the installation
        self.cfg.update('unwanted_env_vars', ['MKLROOT'])
        self.cdftlibs = []
        self.mpi_spec = None

    def prepare_step(self, *args, **kwargs):
        if LooseVersion(self.version) >= LooseVersion('2017.2.174'):
            kwargs['requires_runtime_license'] = False
            super(EB_imkl, self).prepare_step(*args, **kwargs)
        else:
            super(EB_imkl, self).prepare_step(*args, **kwargs)

        # build the mkl interfaces, if desired
        if self.cfg['interfaces']:
            self.cdftlibs = ['fftw2x_cdft']
            if LooseVersion(self.version) >= LooseVersion('10.3'):
                self.cdftlibs.append('fftw3x_cdft')
            # check whether MPI_FAMILY constant is defined, so mpi_family() can be used
            if hasattr(self.toolchain, 'MPI_FAMILY') and self.toolchain.MPI_FAMILY is not None:
                mpi_spec_by_fam = {
                    toolchain.MPICH: 'mpich2',  # MPICH is MPICH v3.x, which is MPICH2 compatible
                    toolchain.MPICH2: 'mpich2',
                    toolchain.MVAPICH2: 'mpich2',
                    toolchain.OPENMPI: 'openmpi',
                }
                mpi_fam = self.toolchain.mpi_family()
                self.mpi_spec = mpi_spec_by_fam.get(mpi_fam)
                debugstr = "MPI toolchain component"
            else:
                # can't use toolchain.mpi_family, because of system toolchain
                if get_software_root('MPICH2') or get_software_root('MVAPICH2'):
                    self.mpi_spec = 'mpich2'
                elif get_software_root('OpenMPI'):
                    self.mpi_spec = 'openmpi'
                elif not get_software_root('impi'):
                    # no compatible MPI found: do not build cdft
                    self.cdftlibs = []
                debugstr = "loaded MPI module"
            if self.mpi_spec:
                self.log.debug("Determined MPI specification based on %s: %s", debugstr, self.mpi_spec)
            else:
                self.log.debug("No MPI or no compatible MPI found: do not build CDFT")

    def install_step(self):
        """
        Actual installation
        - create silent cfg file
        - execute command
        """
        silent_cfg_names_map = None
        silent_cfg_extras = None

        if LooseVersion(self.version) < LooseVersion('11.1'):
            # since imkl v11.1, silent.cfg has been slightly changed to be 'more standard'

            silent_cfg_names_map = {
                'activation_name': ACTIVATION_NAME_2012,
                'license_file_name': LICENSE_FILE_NAME_2012,
            }

        if LooseVersion(self.version) >= LooseVersion('11.1') and self.install_components is None:
            silent_cfg_extras = {
                'COMPONENTS': 'ALL',
            }

        super(EB_imkl, self).install_step(
            silent_cfg_names_map=silent_cfg_names_map,
            silent_cfg_extras=silent_cfg_extras)

    def make_module_req_guess(self):
        """
        A dictionary of possible directories to look for
        """
        guesses = super(EB_imkl, self).make_module_req_guess()

        if LooseVersion(self.version) >= LooseVersion('10.3'):
            if self.cfg['m32']:
                raise EasyBuildError("32-bit not supported yet for IMKL v%s (>= 10.3)", self.version)
            else:
                if LooseVersion(self.version) >= LooseVersion('2021'):
                    compiler_subdir = os.path.join('compiler', self.version, 'linux', 'compiler', 'lib', 'intel64_lin')
                    mkl_subdir = os.path.join('mkl', self.version)
                    pkg_config_path = [os.path.join(mkl_subdir, 'tools', 'pkgconfig')]
                else:
                    compiler_subdir = os.path.join('lib', 'intel64')
                    mkl_subdir = 'mkl'
                    pkg_config_path = [os.path.join(mkl_subdir, 'bin', 'pkgconfig')]
                    guesses['MANPATH'] = ['man', 'man/en_US']
                    if LooseVersion(self.version) >= LooseVersion('11.0'):
                        if LooseVersion(self.version) >= LooseVersion('11.3'):
                            guesses['MIC_LD_LIBRARY_PATH'] = ['lib/intel64_lin_mic', 'mkl/lib/mic']
                        elif LooseVersion(self.version) >= LooseVersion('11.1'):
                            guesses['MIC_LD_LIBRARY_PATH'] = ['lib/mic', 'mkl/lib/mic']
                        else:
                            guesses['MIC_LD_LIBRARY_PATH'] = ['compiler/lib/mic', 'mkl/lib/mic']
                library_path = [compiler_subdir, os.path.join(mkl_subdir, 'lib', 'intel64')]
                cpath = [os.path.join(mkl_subdir, 'include'), os.path.join(mkl_subdir, 'include', 'fftw')]
                guesses.update({
                    'PATH': [],
                    'LD_LIBRARY_PATH': library_path,
                    'LIBRARY_PATH': library_path,
                    'CPATH': cpath,
                    'PKG_CONFIG_PATH': pkg_config_path,
                })
        else:
            if self.cfg['m32']:
                guesses.update({
                    'PATH': ['bin', 'bin/ia32', 'tbb/bin/ia32'],
                    'LD_LIBRARY_PATH': ['lib', 'lib/32'],
                    'LIBRARY_PATH': ['lib', 'lib/32'],
                    'MANPATH': ['man', 'share/man', 'man/en_US'],
                })

            else:
                guesses.update({
                    'PATH': ['bin', 'bin/intel64', 'tbb/bin/em64t'],
                    'LD_LIBRARY_PATH': ['lib', 'lib/em64t'],
                    'LIBRARY_PATH': ['lib', 'lib/em64t'],
                    'MANPATH': ['man', 'share/man', 'man/en_US'],
                })
        return guesses

    def make_module_extra(self):
        """Overwritten from Application to add extra txt"""
        txt = super(EB_imkl, self).make_module_extra()

        if LooseVersion(self.version) >= LooseVersion('2021'):
            mklroot = os.path.join(self.installdir, 'mkl', self.version)
        else:
            mklroot = os.path.join(self.installdir, 'mkl')

        txt += self.module_generator.set_environment('MKLROOT', mklroot)
        return txt

    def post_install_step(self):
        """
        Install group libraries and interfaces (if desired).
        """
        super(EB_imkl, self).post_install_step()

        shlib_ext = get_shared_lib_ext()

        # reload the dependencies
        self.load_dependency_modules()

        if self.cfg['m32']:
            extra = {
                'libmkl.%s' % shlib_ext: 'GROUP (-lmkl_intel -lmkl_intel_thread -lmkl_core)',
                'libmkl_em64t.a': 'GROUP (libmkl_intel.a libmkl_intel_thread.a libmkl_core.a)',
                'libmkl_solver.a': 'GROUP (libmkl_solver.a)',
                'libmkl_scalapack.a': 'GROUP (libmkl_scalapack_core.a)',
                'libmkl_lapack.a': 'GROUP (libmkl_intel.a libmkl_intel_thread.a libmkl_core.a)',
                'libmkl_cdft.a': 'GROUP (libmkl_cdft_core.a)'
            }
        else:
            extra = {
                'libmkl.%s' % shlib_ext: 'GROUP (-lmkl_intel_lp64 -lmkl_intel_thread -lmkl_core)',
                'libmkl_em64t.a': 'GROUP (libmkl_intel_lp64.a libmkl_intel_thread.a libmkl_core.a)',
                'libmkl_solver.a': 'GROUP (libmkl_solver_lp64.a)',
                'libmkl_scalapack.a': 'GROUP (libmkl_scalapack_lp64.a)',
                'libmkl_lapack.a': 'GROUP (libmkl_intel_lp64.a libmkl_intel_thread.a libmkl_core.a)',
                'libmkl_cdft.a': 'GROUP (libmkl_cdft_core.a)'
            }

        loosever = LooseVersion(self.version)

        if loosever >= LooseVersion('2021'):
            libsubdir = os.path.join('mkl', self.version, 'lib', 'intel64')
        elif loosever >= LooseVersion('10.3'):
            libsubdir = os.path.join('mkl', 'lib', 'intel64')
        else:
            if self.cfg['m32']:
                libsubdir = os.path.join('lib', '32')
            else:
                libsubdir = os.path.join('lib', 'em64t')

        for fil, txt in extra.items():
            dest = os.path.join(self.installdir, libsubdir, fil)
            if not os.path.exists(dest):
                write_file(dest, txt)

        # build the mkl interfaces, if desired
        if self.cfg['interfaces']:

            if loosever >= LooseVersion('2021'):
                intsubdir = os.path.join('mkl', self.version, 'interfaces')
                inttarget = 'libintel64'
            elif loosever >= LooseVersion('10.3'):
                intsubdir = os.path.join('mkl', 'interfaces')
                inttarget = 'libintel64'
            else:
                intsubdir = 'interfaces'
                if self.cfg['m32']:
                    inttarget = 'lib32'
                else:
                    inttarget = 'libem64t'

            cmd = "make -f makefile %s" % inttarget

            # blas95 and lapack95 need more work, ignore for now
            # blas95 and lapack also need include/.mod to be processed
            fftw2libs = ['fftw2xc', 'fftw2xf']
            fftw3libs = ['fftw3xc', 'fftw3xf']

            interfacedir = os.path.join(self.installdir, intsubdir)
            change_dir(interfacedir)
            self.log.info("Changed to interfaces directory %s", interfacedir)

            compopt = None
            # determine whether we're using a non-Intel GCC-based or PGI-based toolchain
            # can't use toolchain.comp_family, because of system toolchain used when installing imkl
            if get_software_root('icc') or get_software_root('intel-compilers'):
                compopt = 'compiler=intel'
            else:
                # check for PGI first, since there's a GCC underneath PGI too...
                if get_software_root('PGI'):
                    compopt = 'compiler=pgi'
                elif get_software_root('GCC'):
                    compopt = 'compiler=gnu'
                else:
                    raise EasyBuildError("Not using Intel/GCC/PGI compilers, don't know how to build wrapper libs")

            # patch makefiles for cdft wrappers when PGI is used as compiler
            if get_software_root('PGI'):
                regex_subs = [
                    # pgi should be considered as a valid compiler
                    ("intel gnu", "intel gnu pgi"),
                    # transform 'gnu' case to 'pgi' case
                    (r"ifeq \(\$\(compiler\),gnu\)", "ifeq ($(compiler),pgi)"),
                    ('=gcc', '=pgcc'),
                    # correct flag to use C99 standard
                    ('-std=c99', '-c99'),
                    # -Wall and -Werror are not valid options for pgcc, no close equivalent
                    ('-Wall', ''),
                    ('-Werror', ''),
                ]
                for lib in self.cdftlibs:
                    apply_regex_substitutions(os.path.join(interfacedir, lib, 'makefile'), regex_subs)

            for lib in fftw2libs + fftw3libs + self.cdftlibs:
                buildopts = [compopt]
                if lib in fftw3libs:
                    buildopts.append('install_to=$INSTALL_DIR')
                elif lib in self.cdftlibs:
                    if self.mpi_spec is not None:
                        buildopts.append('mpi=%s' % self.mpi_spec)

                precflags = ['']
                if lib.startswith('fftw2x') and not self.cfg['m32']:
                    # build both single and double precision variants
                    precflags = ['PRECISION=MKL_DOUBLE', 'PRECISION=MKL_SINGLE']

                intflags = ['']
                if lib in self.cdftlibs and not self.cfg['m32']:
                    # build both 32-bit and 64-bit interfaces
                    intflags = ['interface=lp64', 'interface=ilp64']

                allopts = [list(opts) for opts in itertools.product(intflags, precflags)]

                for flags, extraopts in itertools.product(['', '-fPIC'], allopts):
                    tup = (lib, flags, buildopts, extraopts)
                    self.log.debug("Building lib %s with: flags %s, buildopts %s, extraopts %s" % tup)

                    tmpbuild = tempfile.mkdtemp(dir=self.builddir)
                    self.log.debug("Created temporary directory %s" % tmpbuild)

                    # always set INSTALL_DIR, SPEC_OPT, COPTS and CFLAGS
                    # fftw2x(c|f): use $INSTALL_DIR, $CFLAGS and $COPTS
                    # fftw3x(c|f): use $CFLAGS
                    # fftw*cdft: use $INSTALL_DIR and $SPEC_OPT
                    env.setvar('INSTALL_DIR', tmpbuild)
                    env.setvar('SPEC_OPT', flags)
                    env.setvar('COPTS', flags)
                    env.setvar('CFLAGS', flags)

                    try:
                        intdir = os.path.join(interfacedir, lib)
                        os.chdir(intdir)
                        self.log.info("Changed to interface %s directory %s" % (lib, intdir))
                    except OSError as err:
                        raise EasyBuildError("Can't change to interface %s directory %s: %s", lib, intdir, err)

                    fullcmd = "%s %s" % (cmd, ' '.join(buildopts + extraopts))
                    res = run_cmd(fullcmd, log_all=True, simple=True)
                    if not res:
                        raise EasyBuildError("Building %s (flags: %s, fullcmd: %s) failed", lib, flags, fullcmd)

                    for fn in os.listdir(tmpbuild):
                        src = os.path.join(tmpbuild, fn)
                        if flags == '-fPIC':
                            # add _pic to filename
                            ff = fn.split('.')
                            fn = '.'.join(ff[:-1]) + '_pic.' + ff[-1]
                        dest = os.path.join(self.installdir, libsubdir, fn)
                        try:
                            if os.path.isfile(src):
                                shutil.move(src, dest)
                                self.log.info("Moved %s to %s" % (src, dest))
                        except OSError as err:
                            raise EasyBuildError("Failed to move %s to %s: %s", src, dest, err)

                    remove_dir(tmpbuild)

    def sanity_check_step(self):
        """Custom sanity check paths for Intel MKL."""
        shlib_ext = get_shared_lib_ext()

        mklfiles = None
        mkldirs = None
        ver = LooseVersion(self.version)
        libs = ['libmkl_core.%s' % shlib_ext, 'libmkl_gnu_thread.%s' % shlib_ext,
                'libmkl_intel_thread.%s' % shlib_ext, 'libmkl_sequential.%s' % shlib_ext]
        extralibs = ['libmkl_blacs_intelmpi_%(suff)s.' + shlib_ext, 'libmkl_scalapack_%(suff)s.' + shlib_ext]

        if self.cfg['interfaces']:
            if get_software_root('icc') or get_software_root('intel-compilers'):
                compsuff = '_intel'
            # check for PGI first, since there's a GCC underneath PGI too...
            elif get_software_root('PGI'):
                compsuff = '_pgi'
            elif get_software_root('GCC'):
                compsuff = '_gnu'
            else:
                raise EasyBuildError("Not using Intel/GCC/PGI, don't know compiler suffix for FFTW libraries.")

            precs = ['_double', '_single']
            if ver < LooseVersion('11'):
                # no precision suffix in libfftw2 libs before imkl v11
                precs = ['']
            fftw_vers = ['2x%s%s' % (x, prec) for x in ['c', 'f'] for prec in precs] + ['3xc', '3xf']
            pics = ['', '_pic']
            libs += ['libfftw%s%s%s.a' % (fftwver, compsuff, pic) for fftwver in fftw_vers for pic in pics]

            if self.cdftlibs:
                fftw_cdft_vers = ['2x_cdft_DOUBLE']
                if not self.cfg['m32']:
                    fftw_cdft_vers.append('2x_cdft_SINGLE')
                if ver >= LooseVersion('10.3'):
                    fftw_cdft_vers.append('3x_cdft')
                if ver >= LooseVersion('11.0.2'):
                    bits = ['_lp64']
                    if not self.cfg['m32']:
                        bits.append('_ilp64')
                else:
                    # no bits suffix in cdft libs before imkl v11.0.2
                    bits = ['']
                libs += ['libfftw%s%s%s.a' % x for x in itertools.product(fftw_cdft_vers, bits, pics)]

        if ver >= LooseVersion('10.3') and self.cfg['m32']:
            raise EasyBuildError("Sanity check for 32-bit not implemented yet for IMKL v%s (>= 10.3)", self.version)

        if ver >= LooseVersion('2021'):
            basedir = os.path.join('mkl', self.version)

            mkldirs = [
                os.path.join(basedir, 'bin'),
                os.path.join(basedir, 'lib', 'intel64'),
                os.path.join(basedir, 'include'),
            ]
            libs += [lib % {'suff': suff} for lib in extralibs for suff in ['lp64', 'ilp64']]

            mklfiles = [
                os.path.join(basedir, 'lib', 'intel64', 'libmkl_core.%s' % shlib_ext),
                os.path.join(basedir, 'include', 'mkl.h'),
            ]
            mklfiles.extend([os.path.join(basedir, 'lib', 'intel64', lib) for lib in libs])

        elif ver >= LooseVersion('10.3'):
            mkldirs = ['bin', 'mkl/bin', 'mkl/lib/intel64', 'mkl/include']
            if ver < LooseVersion('11.3'):
                mkldirs.append('mkl/bin/intel64')
            libs += [lib % {'suff': suff} for lib in extralibs for suff in ['lp64', 'ilp64']]
            mklfiles = ['mkl/lib/intel64/libmkl.%s' % shlib_ext, 'mkl/include/mkl.h'] + \
                       ['mkl/lib/intel64/%s' % lib for lib in libs]
            if ver >= LooseVersion('10.3.4') and ver < LooseVersion('11.1'):
                mkldirs += ['compiler/lib/intel64']
            else:
                if ver >= LooseVersion('2017.0.0'):
                    mkldirs += ['lib/intel64_lin']
                else:
                    mkldirs += ['lib/intel64']

        else:
            if self.cfg['m32']:
                mklfiles = ['lib/32/libmkl.%s' % shlib_ext, 'include/mkl.h'] + \
                           ['lib/32/%s' % lib for lib in libs]
                mkldirs = ['lib/32', 'include/32', 'interfaces']
            else:
                libs += [lib % {'suff': suff} for lib in extralibs for suff in ['lp64', 'ilp64']]
                mklfiles = ['lib/em64t/libmkl.%s' % shlib_ext, 'include/mkl.h'] + \
                           ['lib/em64t/%s' % lib for lib in libs]
                mkldirs = ['lib/em64t', 'include/em64t', 'interfaces']

        custom_paths = {
            'files': mklfiles,
            'dirs': mkldirs,
        }

        super(EB_imkl, self).sanity_check_step(custom_paths=custom_paths)

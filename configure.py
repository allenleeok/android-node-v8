from __future__ import print_function

import json
import sys
import errno
import optparse
import os
import pipes
import pprint
import re
import shlex
import subprocess
import shutil
import string
from distutils.spawn import find_executable as which

# If not run from node/, cd to node/.
os.chdir(os.path.dirname(__file__) or '.')

original_argv = sys.argv[1:]

# gcc and g++ as defaults matches what GYP's Makefile generator does,
# except on OS X.
CC = os.environ.get('CC', 'cc' if sys.platform == 'darwin' else 'gcc')
CXX = os.environ.get('CXX', 'c++' if sys.platform == 'darwin' else 'g++')

sys.path.insert(0, os.path.join('tools', 'gyp', 'pylib'))
from gyp.common import GetFlavor

# imports in tools/configure.d
sys.path.insert(0, os.path.join('tools', 'configure.d'))
import nodedownload

# imports in tools/
sys.path.insert(0, 'tools')
import getmoduleversion
import getnapibuildversion
from gyp_node import run_gyp

# imports in deps/v8/tools/node
sys.path.insert(0, os.path.join('deps', 'v8', 'tools', 'node'))
from fetch_deps import FetchDeps

# parse our options
parser = optparse.OptionParser()

valid_os = ('win', 'mac', 'solaris', 'freebsd', 'openbsd', 'linux',
            'android', 'aix', 'cloudabi')
valid_arch = ('arm', 'arm64', 'ia32', 'mips', 'mipsel', 'mips64el', 'ppc',
              'ppc64', 'x32','x64', 'x86', 'x86_64', 's390', 's390x')
valid_arm_float_abi = ('soft', 'softfp', 'hard')
valid_arm_fpu = ('vfp', 'vfpv3', 'vfpv3-d16', 'neon')
valid_mips_arch = ('loongson', 'r1', 'r2', 'r6', 'rx')
valid_mips_fpu = ('fp32', 'fp64', 'fpxx')
valid_mips_float_abi = ('soft', 'hard')
valid_intl_modes = ('none', 'small-icu', 'full-icu', 'system-icu')
with open ('tools/icu/icu_versions.json') as f:
  icu_versions = json.load(f)

# create option groups
shared_optgroup = optparse.OptionGroup(parser, "Shared libraries",
    "Flags that allows you to control whether you want to build against "
    "built-in dependencies or its shared representations. If necessary, "
    "provide multiple libraries with comma.")
intl_optgroup = optparse.OptionGroup(parser, "Internationalization",
    "Flags that lets you enable i18n features in Node.js as well as which "
    "library you want to build against.")
http2_optgroup = optparse.OptionGroup(parser, "HTTP2",
    "Flags that allows you to control HTTP2 features in Node.js")

# Options should be in alphabetical order but keep --prefix at the top,
# that's arguably the one people will be looking for most.
parser.add_option('--prefix',
    action='store',
    dest='prefix',
    default='/usr/local',
    help='select the install prefix [default: %default]')

parser.add_option('--coverage',
    action='store_true',
    dest='coverage',
    help='Build node with code coverage enabled')

parser.add_option('--debug',
    action='store_true',
    dest='debug',
    help='also build debug build')
    
parser.add_option('--debug-node',
    action='store_true',
    dest='debug_node',
    help='build the Node.js part of the binary with debugging symbols')    

parser.add_option('--dest-cpu',
    action='store',
    dest='dest_cpu',
    choices=valid_arch,
    help='CPU architecture to build for ({0})'.format(', '.join(valid_arch)))

parser.add_option('--cross-compiling',
    action='store_true',
    dest='cross_compiling',
    default=None,
    help='force build to be considered as cross compiled')
parser.add_option('--no-cross-compiling',
    action='store_false',
    dest='cross_compiling',
    default=None,
    help='force build to be considered as NOT cross compiled')

parser.add_option('--dest-os',
    action='store',
    dest='dest_os',
    choices=valid_os,
    help='operating system to build for ({0})'.format(', '.join(valid_os)))

parser.add_option('--gdb',
    action='store_true',
    dest='gdb',
    help='add gdb support')

parser.add_option('--no-ifaddrs',
    action='store_true',
    dest='no_ifaddrs',
    help='use on deprecated SunOS systems that do not support ifaddrs.h')

parser.add_option("--fully-static",
    action="store_true",
    dest="fully_static",
    help="Generate an executable without external dynamic libraries. This "
         "will not work on OSX when using the default compilation environment")

parser.add_option("--partly-static",
    action="store_true",
    dest="partly_static",
    help="Generate an executable with libgcc and libstdc++ libraries. This "
         "will not work on OSX when using the default compilation environment")

parser.add_option("--enable-pgo-generate",
    action="store_true",
    dest="enable_pgo_generate",
    help="Enable profiling with pgo of a binary. This feature is only available "
         "on linux with gcc and g++ 5.4.1 or newer.")

parser.add_option("--enable-pgo-use",
    action="store_true",
    dest="enable_pgo_use",
    help="Enable use of the profile generated with --enable-pgo-generate. This "
         "feature is only available on linux with gcc and g++ 5.4.1 or newer.")

parser.add_option("--enable-lto",
    action="store_true",
    dest="enable_lto",
    help="Enable compiling with lto of a binary. This feature is only available "
         "on linux with gcc and g++ 5.4.1 or newer.")

parser.add_option("--link-module",
    action="append",
    dest="linked_module",
    help="Path to a JS file to be bundled in the binary as a builtin. "
         "This module will be referenced by path without extension; "
         "e.g. /root/x/y.js will be referenced via require('root/x/y'). "
         "Can be used multiple times")

parser.add_option("--openssl-no-asm",
    action="store_true",
    dest="openssl_no_asm",
    help="Do not build optimized assembly for OpenSSL")

parser.add_option('--openssl-fips',
    action='store',
    dest='openssl_fips',
    help='Build OpenSSL using FIPS canister .o file in supplied folder')

parser.add_option('--openssl-is-fips',
    action='store_true',
    dest='openssl_is_fips',
    help='specifies that the OpenSSL library is FIPS compatible')

parser.add_option('--openssl-use-def-ca-store',
    action='store_true',
    dest='use_openssl_ca_store',
    help='Use OpenSSL supplied CA store instead of compiled-in Mozilla CA copy.')

parser.add_option('--openssl-system-ca-path',
    action='store',
    dest='openssl_system_ca_path',
    help='Use the specified path to system CA (PEM format) in addition to '
         'the OpenSSL supplied CA store or compiled-in Mozilla CA copy.')

parser.add_option('--experimental-http-parser',
    action='store_true',
    dest='experimental_http_parser',
    help='(no-op)')

shared_optgroup.add_option('--shared-http-parser',
    action='store_true',
    dest='shared_http_parser',
    help='link to a shared http_parser DLL instead of static linking')

shared_optgroup.add_option('--shared-http-parser-includes',
    action='store',
    dest='shared_http_parser_includes',
    help='directory containing http_parser header files')

shared_optgroup.add_option('--shared-http-parser-libname',
    action='store',
    dest='shared_http_parser_libname',
    default='http_parser',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-http-parser-libpath',
    action='store',
    dest='shared_http_parser_libpath',
    help='a directory to search for the shared http_parser DLL')

shared_optgroup.add_option('--shared-libuv',
    action='store_true',
    dest='shared_libuv',
    help='link to a shared libuv DLL instead of static linking')

shared_optgroup.add_option('--shared-libuv-includes',
    action='store',
    dest='shared_libuv_includes',
    help='directory containing libuv header files')

shared_optgroup.add_option('--shared-libuv-libname',
    action='store',
    dest='shared_libuv_libname',
    default='uv',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-libuv-libpath',
    action='store',
    dest='shared_libuv_libpath',
    help='a directory to search for the shared libuv DLL')

shared_optgroup.add_option('--shared-nghttp2',
    action='store_true',
    dest='shared_nghttp2',
    help='link to a shared nghttp2 DLL instead of static linking')

shared_optgroup.add_option('--shared-nghttp2-includes',
    action='store',
    dest='shared_nghttp2_includes',
    help='directory containing nghttp2 header files')

shared_optgroup.add_option('--shared-nghttp2-libname',
    action='store',
    dest='shared_nghttp2_libname',
    default='nghttp2',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-nghttp2-libpath',
    action='store',
    dest='shared_nghttp2_libpath',
    help='a directory to search for the shared nghttp2 DLLs')

shared_optgroup.add_option('--shared-openssl',
    action='store_true',
    dest='shared_openssl',
    help='link to a shared OpenSSl DLL instead of static linking')

shared_optgroup.add_option('--shared-openssl-includes',
    action='store',
    dest='shared_openssl_includes',
    help='directory containing OpenSSL header files')

shared_optgroup.add_option('--shared-openssl-libname',
    action='store',
    dest='shared_openssl_libname',
    default='crypto,ssl',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-openssl-libpath',
    action='store',
    dest='shared_openssl_libpath',
    help='a directory to search for the shared OpenSSL DLLs')

shared_optgroup.add_option('--shared-zlib',
    action='store_true',
    dest='shared_zlib',
    help='link to a shared zlib DLL instead of static linking')

shared_optgroup.add_option('--shared-zlib-includes',
    action='store',
    dest='shared_zlib_includes',
    help='directory containing zlib header files')

shared_optgroup.add_option('--shared-zlib-libname',
    action='store',
    dest='shared_zlib_libname',
    default='z',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-zlib-libpath',
    action='store',
    dest='shared_zlib_libpath',
    help='a directory to search for the shared zlib DLL')

shared_optgroup.add_option('--shared-cares',
    action='store_true',
    dest='shared_libcares',
    help='link to a shared cares DLL instead of static linking')

shared_optgroup.add_option('--shared-cares-includes',
    action='store',
    dest='shared_libcares_includes',
    help='directory containing cares header files')

shared_optgroup.add_option('--shared-cares-libname',
    action='store',
    dest='shared_libcares_libname',
    default='cares',
    help='alternative lib name to link to [default: %default]')

shared_optgroup.add_option('--shared-cares-libpath',
    action='store',
    dest='shared_libcares_libpath',
    help='a directory to search for the shared cares DLL')

parser.add_option_group(shared_optgroup)

parser.add_option('--systemtap-includes',
    action='store',
    dest='systemtap_includes',
    help='directory containing systemtap header files')

parser.add_option('--tag',
    action='store',
    dest='tag',
    help='custom build tag')

parser.add_option('--release-urlbase',
    action='store',
    dest='release_urlbase',
    help='Provide a custom URL prefix for the `process.release` properties '
         '`sourceUrl` and `headersUrl`. When compiling a release build, this '
         'will default to https://nodejs.org/download/release/')

parser.add_option('--enable-d8',
    action='store_true',
    dest='enable_d8',
    help=optparse.SUPPRESS_HELP)  # Unsupported, undocumented.

parser.add_option('--enable-trace-maps',
    action='store_true',
    dest='trace_maps',
    help='Enable the --trace-maps flag in V8 (use at your own risk)')

parser.add_option('--v8-options',
    action='store',
    dest='v8_options',
    help='v8 options to pass, see `node --v8-options` for examples.')

parser.add_option('--with-arm-float-abi',
    action='store',
    dest='arm_float_abi',
    choices=valid_arm_float_abi,
    help='specifies which floating-point ABI to use ({0}).'.format(
        ', '.join(valid_arm_float_abi)))

parser.add_option('--with-arm-fpu',
    action='store',
    dest='arm_fpu',
    choices=valid_arm_fpu,
    help='ARM FPU mode ({0}) [default: %default]'.format(
        ', '.join(valid_arm_fpu)))

parser.add_option('--with-mips-arch-variant',
    action='store',
    dest='mips_arch_variant',
    default='r2',
    choices=valid_mips_arch,
    help='MIPS arch variant ({0}) [default: %default]'.format(
        ', '.join(valid_mips_arch)))

parser.add_option('--with-mips-fpu-mode',
    action='store',
    dest='mips_fpu_mode',
    default='fp32',
    choices=valid_mips_fpu,
    help='MIPS FPU mode ({0}) [default: %default]'.format(
        ', '.join(valid_mips_fpu)))

parser.add_option('--with-mips-float-abi',
    action='store',
    dest='mips_float_abi',
    default='hard',
    choices=valid_mips_float_abi,
    help='MIPS floating-point ABI ({0}) [default: %default]'.format(
        ', '.join(valid_mips_float_abi)))

parser.add_option('--with-dtrace',
    action='store_true',
    dest='with_dtrace',
    help='build with DTrace (default is true on sunos and darwin)')

parser.add_option('--with-etw',
    action='store_true',
    dest='with_etw',
    help='build with ETW (default is true on Windows)')

parser.add_option('--use-largepages',
    action='store_true',
    dest='node_use_large_pages',
    help='build with Large Pages support. This feature is supported only on Linux kernel' +
         '>= 2.6.38 with Transparent Huge pages enabled and FreeBSD')

intl_optgroup.add_option('--with-intl',
    action='store',
    dest='with_intl',
    default='small-icu',
    choices=valid_intl_modes,
    help='Intl mode (valid choices: {0}) [default: %default]'.format(
        ', '.join(valid_intl_modes)))

intl_optgroup.add_option('--without-intl',
    action='store_const',
    dest='with_intl',
    const='none',
    help='Disable Intl, same as --with-intl=none (disables inspector)')

intl_optgroup.add_option('--with-icu-path',
    action='store',
    dest='with_icu_path',
    help='Path to icu.gyp (ICU i18n, Chromium version only.)')

icu_default_locales='root,en'

intl_optgroup.add_option('--with-icu-locales',
    action='store',
    dest='with_icu_locales',
    default=icu_default_locales,
    help='Comma-separated list of locales for "small-icu". "root" is assumed. '
        '[default: %default]')

intl_optgroup.add_option('--with-icu-source',
    action='store',
    dest='with_icu_source',
    help='Intl mode: optional local path to icu/ dir, or path/URL of '
        'the icu4c source archive. '
        'v%d.x or later recommended.' % icu_versions['minimum_icu'])

parser.add_option('--with-ltcg',
    action='store_true',
    dest='with_ltcg',
    help='Use Link Time Code Generation. This feature is only available on Windows.')

parser.add_option('--without-node-snapshot',
    action='store_true',
    dest='without_node_snapshot',
    help='Turn off V8 snapshot integration. Currently experimental.')

parser.add_option('--without-node-code-cache',
    action='store_true',
    dest='without_node_code_cache',
    help='Turn off V8 Code cache integration.')

intl_optgroup.add_option('--download',
    action='store',
    dest='download_list',
    help=nodedownload.help())

intl_optgroup.add_option('--download-path',
    action='store',
    dest='download_path',
    default='deps',
    help='Download directory [default: %default]')

parser.add_option_group(intl_optgroup)

parser.add_option('--debug-lib',
    action='store_true',
    dest='node_debug_lib',
    help='build lib with DCHECK macros')

http2_optgroup.add_option('--debug-nghttp2',
    action='store_true',
    dest='debug_nghttp2',
    help='build nghttp2 with DEBUGBUILD (default is false)')

parser.add_option_group(http2_optgroup)

parser.add_option('--without-dtrace',
    action='store_true',
    dest='without_dtrace',
    help='build without DTrace')

parser.add_option('--without-etw',
    action='store_true',
    dest='without_etw',
    help='build without ETW')

parser.add_option('--without-npm',
    action='store_true',
    dest='without_npm',
    help='do not install the bundled npm (package manager)')

parser.add_option('--without-report',
    action='store_true',
    dest='without_report',
    help='build without report')

# Dummy option for backwards compatibility
parser.add_option('--with-snapshot',
    action='store_true',
    dest='unused_with_snapshot',
    help=optparse.SUPPRESS_HELP)

parser.add_option('--without-snapshot',
    action='store_true',
    dest='without_snapshot',
    help=optparse.SUPPRESS_HELP)

parser.add_option('--without-siphash',
    action='store_true',
    dest='without_siphash',
    help=optparse.SUPPRESS_HELP)

# End dummy list.

parser.add_option('--without-ssl',
    action='store_true',
    dest='without_ssl',
    help='build without SSL (disables crypto, https, inspector, etc.)')

parser.add_option('--without-node-options',
    action='store_true',
    dest='without_node_options',
    help='build without NODE_OPTIONS support')

parser.add_option('--ninja',
    action='store_true',
    dest='use_ninja',
    help='generate build files for use with Ninja')

parser.add_option('--enable-asan',
    action='store_true',
    dest='enable_asan',
    help='build with asan')

parser.add_option('--enable-static',
    action='store_true',
    dest='enable_static',
    help='build as static library')

parser.add_option('--no-browser-globals',
    action='store_true',
    dest='no_browser_globals',
    help='do not export browser globals like setTimeout, console, etc. ' +
         '(This mode is not officially supported for regular applications)')

parser.add_option('--without-inspector',
    action='store_true',
    dest='without_inspector',
    help='disable the V8 inspector protocol')

parser.add_option('--shared',
    action='store_true',
    dest='shared',
    help='compile shared library for embedding node in another project. ' +
         '(This mode is not officially supported for regular applications)')

parser.add_option('--without-v8-platform',
    action='store_true',
    dest='without_v8_platform',
    default=False,
    help='do not initialize v8 platform during node.js startup. ' +
         '(This mode is not officially supported for regular applications)')

parser.add_option('--without-bundled-v8',
    action='store_true',
    dest='without_bundled_v8',
    default=False,
    help='do not use V8 includes from the bundled deps folder. ' +
         '(This mode is not officially supported for regular applications)')

parser.add_option('--build-v8-with-gn',
    action='store_true',
    dest='build_v8_with_gn',
    default=False,
    help='build V8 using GN instead of gyp')

parser.add_option('--verbose',
    action='store_true',
    dest='verbose',
    default=False,
    help='get more output from this script')

parser.add_option('--v8-non-optimized-debug',
    action='store_true',
    dest='v8_non_optimized_debug',
    default=False,
    help='compile V8 with minimal optimizations and with runtime checks')

# Create compile_commands.json in out/Debug and out/Release.
parser.add_option('-C',
    action='store_true',
    dest='compile_commands_json',
    help=optparse.SUPPRESS_HELP)

(options, args) = parser.parse_args()

# Expand ~ in the install prefix now, it gets written to multiple files.
options.prefix = os.path.expanduser(options.prefix or '')

# set up auto-download list
auto_downloads = nodedownload.parse(options.download_list)


def error(msg):
  prefix = '\033[1m\033[31mERROR\033[0m' if os.isatty(1) else 'ERROR'
  print('%s: %s' % (prefix, msg))
  sys.exit(1)

def warn(msg):
  warn.warned = True
  prefix = '\033[1m\033[93mWARNING\033[0m' if os.isatty(1) else 'WARNING'
  print('%s: %s' % (prefix, msg))

# track if warnings occurred
warn.warned = False

def info(msg):
  prefix = '\033[1m\033[32mINFO\033[0m' if os.isatty(1) else 'INFO'
  print('%s: %s' % (prefix, msg))

def print_verbose(x):
  if not options.verbose:
    return
  if type(x) is str:
    print(x)
  else:
    pprint.pprint(x, indent=2)

def b(value):
  """Returns the string 'true' if value is truthy, 'false' otherwise."""
  if value:
    return 'true'
  else:
    return 'false'

def B(value):
  """Returns 1 if value is truthy, 0 otherwise."""
  if value:
    return 1
  else:
    return 0


def pkg_config(pkg):
  """Run pkg-config on the specified package
  Returns ("-l flags", "-I flags", "-L flags", "version")
  otherwise (None, None, None, None)"""
  pkg_config = os.environ.get('PKG_CONFIG', 'pkg-config')
  args = []  # Print pkg-config warnings on first round.
  retval = ()
  for flag in ['--libs-only-l', '--cflags-only-I',
               '--libs-only-L', '--modversion']:
    args += [flag, pkg]
    try:
      proc = subprocess.Popen(shlex.split(pkg_config) + args,
                              stdout=subprocess.PIPE)
      val = proc.communicate()[0].strip()
    except OSError as e:
      if e.errno != errno.ENOENT: raise e  # Unexpected error.
      return (None, None, None, None)  # No pkg-config/pkgconf installed.
    retval += (val,)
    args = ['--silence-errors']
  return retval


def try_check_compiler(cc, lang):
  try:
    proc = subprocess.Popen(shlex.split(cc) + ['-E', '-P', '-x', lang, '-'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
  except OSError:
    return (False, False, '', '')

  proc.stdin.write('__clang__ __GNUC__ __GNUC_MINOR__ __GNUC_PATCHLEVEL__ '
                   '__clang_major__ __clang_minor__ __clang_patchlevel__')

  values = (proc.communicate()[0].split() + ['0'] * 7)[0:7]
  is_clang = values[0] == '1'
  gcc_version = tuple(map(int, values[1:1+3]))
  clang_version = tuple(map(int, values[4:4+3])) if is_clang else None

  return (True, is_clang, clang_version, gcc_version)


#
# The version of asm compiler is needed for building openssl asm files.
# See deps/openssl/openssl.gypi for detail.
# Commands and regular expressions to obtain its version number are taken from
# https://github.com/openssl/openssl/blob/OpenSSL_1_0_2-stable/crypto/sha/asm/sha512-x86_64.pl#L112-L129
#
def get_version_helper(cc, regexp):
  try:
    proc = subprocess.Popen(shlex.split(cc) + ['-v'], stdin=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdout=subprocess.PIPE)
  except OSError:
    error('''No acceptable C compiler found!

       Please make sure you have a C compiler installed on your system and/or
       consider adjusting the CC environment variable if you installed
       it in a non-standard prefix.''')

  match = re.search(regexp, proc.communicate()[1])

  if match:
    return match.group(2)
  else:
    return '0'

def get_nasm_version(asm):
  try:
    proc = subprocess.Popen(shlex.split(asm) + ['-v'],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE)
  except OSError:
    warn('''No acceptable ASM compiler found!
         Please make sure you have installed NASM from https://www.nasm.us
         and refer BUILDING.md.''')
    return '0'

  match = re.match(r"NASM version ([2-9]\.[0-9][0-9]+)",
                   proc.communicate()[0])

  if match:
    return match.group(1)
  else:
    return '0'

def get_llvm_version(cc):
  return get_version_helper(
    cc, r"(^(?:FreeBSD )?clang version|based on LLVM) ([3-9]\.[0-9]+)")

def get_xcode_version(cc):
  return get_version_helper(
    cc, r"(^Apple (?:clang|LLVM) version) ([0-9]+\.[0-9]+)")

def get_gas_version(cc):
  try:
    custom_env = os.environ.copy()
    custom_env["LC_ALL"] = "C"
    proc = subprocess.Popen(shlex.split(cc) + ['-Wa,-v', '-c', '-o',
                                               '/dev/null', '-x',
                                               'assembler',  '/dev/null'],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdout=subprocess.PIPE, env=custom_env)
  except OSError:
    error('''No acceptable C compiler found!

       Please make sure you have a C compiler installed on your system and/or
       consider adjusting the CC environment variable if you installed
       it in a non-standard prefix.''')

  gas_ret = proc.communicate()[1]
  match = re.match(r"GNU assembler version ([2-9]\.[0-9]+)", gas_ret)

  if match:
    return match.group(1)
  else:
    warn('Could not recognize `gas`: ' + gas_ret)
    return '0'

# Note: Apple clang self-reports as clang 4.2.0 and gcc 4.2.1.  It passes
# the version check more by accident than anything else but a more rigorous
# check involves checking the build number against a whitelist.  I'm not
# quite prepared to go that far yet.
def check_compiler(o):
  if sys.platform == 'win32':
    if not options.openssl_no_asm and options.dest_cpu in ('x86', 'x64'):
      nasm_version = get_nasm_version('nasm')
      o['variables']['nasm_version'] = nasm_version
      if nasm_version == 0:
        o['variables']['openssl_no_asm'] = 1
    return

  ok, is_clang, clang_version, gcc_version = try_check_compiler(CXX, 'c++')
  if not ok:
    warn('failed to autodetect C++ compiler version (CXX=%s)' % CXX)
  elif clang_version < (8, 0, 0) if is_clang else gcc_version < (6, 3, 0):
    warn('C++ compiler too old, need g++ 6.3.0 or clang++ 8.0.0 (CXX=%s)' % CXX)

  ok, is_clang, clang_version, gcc_version = try_check_compiler(CC, 'c')
  if not ok:
    warn('failed to autodetect C compiler version (CC=%s)' % CC)
  elif not is_clang and gcc_version < (4, 2, 0):
    # clang 3.2 is a little white lie because any clang version will probably
    # do for the C bits.  However, we might as well encourage people to upgrade
    # to a version that is not completely ancient.
    warn('C compiler too old, need gcc 4.2 or clang 3.2 (CC=%s)' % CC)

  o['variables']['llvm_version'] = get_llvm_version(CC) if is_clang else 0

  # Need xcode_version or gas_version when openssl asm files are compiled.
  if options.without_ssl or options.openssl_no_asm or options.shared_openssl:
    return

  if is_clang:
    if sys.platform == 'darwin':
      o['variables']['xcode_version'] = get_xcode_version(CC)
  else:
    o['variables']['gas_version'] = get_gas_version(CC)


def cc_macros(cc=None):
  """Checks predefined macros using the C compiler command."""

  try:
    p = subprocess.Popen(shlex.split(cc or CC) + ['-dM', '-E', '-'],
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
  except OSError:
    error('''No acceptable C compiler found!

       Please make sure you have a C compiler installed on your system and/or
       consider adjusting the CC environment variable if you installed
       it in a non-standard prefix.''')

  p.stdin.write('\n')
  out = p.communicate()[0]

  out = str(out).split('\n')

  k = {}
  for line in out:
    lst = shlex.split(line)
    if len(lst) > 2:
      key = lst[1]
      val = lst[2]
      k[key] = val
  return k


def is_arch_armv7():
  """Check for ARMv7 instructions"""
  cc_macros_cache = cc_macros()
  return cc_macros_cache.get('__ARM_ARCH') == '7'


def is_arch_armv6():
  """Check for ARMv6 instructions"""
  cc_macros_cache = cc_macros()
  return cc_macros_cache.get('__ARM_ARCH') == '6'


def is_arm_hard_float_abi():
  """Check for hardfloat or softfloat eabi on ARM"""
  # GCC versions 4.6 and above define __ARM_PCS or __ARM_PCS_VFP to specify
  # the Floating Point ABI used (PCS stands for Procedure Call Standard).
  # We use these as well as a couple of other defines to statically determine
  # what FP ABI used.

  return '__ARM_PCS_VFP' in cc_macros()


def host_arch_cc():
  """Host architecture check using the CC command."""

  if sys.platform.startswith('aix'):
    # we only support gcc at this point and the default on AIX
    # would be xlc so hard code gcc
    k = cc_macros('gcc')
  else:
    k = cc_macros(os.environ.get('CC_host'))

  matchup = {
    '__aarch64__' : 'arm64',
    '__arm__'     : 'arm',
    '__i386__'    : 'ia32',
    '__MIPSEL__'  : 'mipsel',
    '__mips__'    : 'mips',
    '__PPC64__'   : 'ppc64',
    '__PPC__'     : 'ppc64',
    '__x86_64__'  : 'x64',
    '__s390__'    : 's390',
    '__s390x__'   : 's390x',
  }

  rtn = 'ia32' # default

  for i in matchup:
    if i in k and k[i] != '0':
      rtn = matchup[i]
      if rtn != 's390':
        break

  if rtn == 'mipsel' and '_LP64' in k:
    rtn = 'mips64el'

  return rtn


def host_arch_win():
  """Host architecture check using environ vars (better way to do this?)"""

  observed_arch = os.environ.get('PROCESSOR_ARCHITECTURE', 'x86')
  arch = os.environ.get('PROCESSOR_ARCHITEW6432', observed_arch)

  matchup = {
    'AMD64'  : 'x64',
    'x86'    : 'ia32',
    'arm'    : 'arm',
    'mips'   : 'mips',
  }

  return matchup.get(arch, 'ia32')


def configure_arm(o):
  if options.arm_float_abi:
    arm_float_abi = options.arm_float_abi
  elif is_arm_hard_float_abi():
    arm_float_abi = 'hard'
  else:
    arm_float_abi = 'default'

  arm_fpu = 'vfp'

  if is_arch_armv7():
    arm_fpu = 'vfpv3'
    o['variables']['arm_version'] = '7'
  else:
    o['variables']['arm_version'] = '6' if is_arch_armv6() else 'default'

  o['variables']['arm_thumb'] = 0      # -marm
  o['variables']['arm_float_abi'] = arm_float_abi

  if options.dest_os == 'android':
    arm_fpu = 'vfpv3'
    o['variables']['arm_version'] = '7'

  o['variables']['arm_fpu'] = options.arm_fpu or arm_fpu


def configure_mips(o):
  can_use_fpu_instructions = (options.mips_float_abi != 'soft')
  o['variables']['v8_can_use_fpu_instructions'] = b(can_use_fpu_instructions)
  o['variables']['v8_use_mips_abi_hardfloat'] = b(can_use_fpu_instructions)
  o['variables']['mips_arch_variant'] = options.mips_arch_variant
  o['variables']['mips_fpu_mode'] = options.mips_fpu_mode


def gcc_version_ge(version_checked):
  for compiler in [(CC, 'c'), (CXX, 'c++')]:
    ok, is_clang, clang_version, compiler_version = \
      try_check_compiler(compiler[0], compiler[1])
    if is_clang or compiler_version < version_checked:
      return False
  return True


def configure_node(o):
  if options.dest_os == 'android':
    o['variables']['OS'] = 'android'
  o['variables']['node_prefix'] = options.prefix
  o['variables']['node_install_npm'] = b(not options.without_npm)
  o['variables']['node_report'] = b(not options.without_report)
  o['variables']['debug_node'] = b(options.debug_node)
  o['default_configuration'] = 'Debug' if options.debug else 'Release'

  host_arch = host_arch_win() if os.name == 'nt' else host_arch_cc()
  target_arch = options.dest_cpu or host_arch
  # ia32 is preferred by the build tools (GYP) over x86 even if we prefer the latter
  # the Makefile resets this to x86 afterward
  if target_arch == 'x86':
    target_arch = 'ia32'
  # x86_64 is common across linuxes, allow it as an alias for x64
  if target_arch == 'x86_64':
    target_arch = 'x64'
  o['variables']['host_arch'] = host_arch
  o['variables']['target_arch'] = target_arch
  o['variables']['node_byteorder'] = sys.byteorder

  cross_compiling = (options.cross_compiling
                     if options.cross_compiling is not None
                     else target_arch != host_arch)
  want_snapshots = not options.without_snapshot
  o['variables']['want_separate_host_toolset'] = int(
      cross_compiling and want_snapshots)

  if not options.without_node_snapshot:
    o['variables']['node_use_node_snapshot'] = b(not cross_compiling and not options.shared)
  else:
    o['variables']['node_use_node_snapshot'] = 'false'

  if not options.without_node_code_cache:
    # TODO(refack): fix this when implementing embedded code-cache when cross-compiling.
    o['variables']['node_use_node_code_cache'] = b(not cross_compiling and not options.shared)
  else:
    o['variables']['node_use_node_code_cache'] = 'false'  

  if target_arch == 'arm':
    configure_arm(o)
  elif target_arch in ('mips', 'mipsel', 'mips64el'):
    configure_mips(o)

  if flavor == 'aix':
    o['variables']['node_target_type'] = 'static_library'

  if flavor != 'linux' and (options.enable_pgo_generate or options.enable_pgo_use):
    raise Exception(
      'The pgo option is supported only on linux.')

  if flavor == 'linux':
    if options.enable_pgo_generate or options.enable_pgo_use:
      version_checked = (5, 4, 1)
      if not gcc_version_ge(version_checked):
        version_checked_str = ".".join(map(str, version_checked))
        raise Exception(
          'The options --enable-pgo-generate and --enable-pgo-use '
          'are supported for gcc and gxx %s or newer only.' % (version_checked_str))

    if options.enable_pgo_generate and options.enable_pgo_use:
      raise Exception(
        'Only one of the --enable-pgo-generate or --enable-pgo-use options '
        'can be specified at a time. You would like to use '
        '--enable-pgo-generate first, profile node, and then recompile '
        'with --enable-pgo-use')

  o['variables']['enable_pgo_generate'] = b(options.enable_pgo_generate)
  o['variables']['enable_pgo_use']      = b(options.enable_pgo_use)

  if flavor != 'linux' and (options.enable_lto):
    raise Exception(
      'The lto option is supported only on linux.')

  if flavor == 'linux':
    if options.enable_lto:
      version_checked = (5, 4, 1)
      if not gcc_version_ge(version_checked):
        version_checked_str = ".".join(map(str, version_checked))
        raise Exception(
          'The option --enable-lto is supported for gcc and gxx %s'
          ' or newer only.' % (version_checked_str))

  o['variables']['enable_lto'] = b(options.enable_lto)

  if flavor in ('solaris', 'mac', 'linux', 'freebsd'):
    use_dtrace = not options.without_dtrace
    # Don't enable by default on linux and freebsd
    if flavor in ('linux', 'freebsd'):
      use_dtrace = options.with_dtrace

    if flavor == 'linux':
      if options.systemtap_includes:
        o['include_dirs'] += [options.systemtap_includes]
    o['variables']['node_use_dtrace'] = b(use_dtrace)
  elif options.with_dtrace:
    raise Exception(
       'DTrace is currently only supported on SunOS, MacOS or Linux systems.')
  else:
    o['variables']['node_use_dtrace'] = 'false'

  if options.node_use_large_pages and not flavor in ('linux', 'freebsd'):
    raise Exception(
      'Large pages are supported only on Linux Systems.')
  if options.node_use_large_pages and flavor in ('linux', 'freebsd'):
    if options.shared or options.enable_static:
      raise Exception(
        'Large pages are supported only while creating node executable.')
    if target_arch!="x64":
      raise Exception(
        'Large pages are supported only x64 platform.')
    if flavor == 'linux':
      # Example full version string: 2.6.32-696.28.1.el6.x86_64
      FULL_KERNEL_VERSION=os.uname()[2]
      KERNEL_VERSION=FULL_KERNEL_VERSION.split('-')[0]
      if KERNEL_VERSION < "2.6.38" and flavor == 'linux':
        raise Exception(
          'Large pages need Linux kernel version >= 2.6.38')
  o['variables']['node_use_large_pages'] = b(options.node_use_large_pages)

  if options.no_ifaddrs:
    o['defines'] += ['SUNOS_NO_IFADDRS']

  # By default, enable ETW on Windows.
  if flavor == 'win':
    o['variables']['node_use_etw'] = b(not options.without_etw)
  elif options.with_etw:
    raise Exception('ETW is only supported on Windows.')
  else:
    o['variables']['node_use_etw'] = 'false'

  o['variables']['node_with_ltcg'] = b(options.with_ltcg)
  if flavor != 'win' and options.with_ltcg:
    raise Exception('Link Time Code Generation is only supported on Windows.')

  if options.tag:
    o['variables']['node_tag'] = '-' + options.tag
  else:
    o['variables']['node_tag'] = ''

  o['variables']['node_release_urlbase'] = options.release_urlbase or ''

  if options.v8_options:
    o['variables']['node_v8_options'] = options.v8_options.replace('"', '\\"')

  if options.enable_static:
    o['variables']['node_target_type'] = 'static_library'

  o['variables']['node_debug_lib'] = b(options.node_debug_lib)

  if options.debug_nghttp2:
    o['variables']['debug_nghttp2'] = 1
  else:
    o['variables']['debug_nghttp2'] = 'false'

  o['variables']['node_no_browser_globals'] = b(options.no_browser_globals)

  o['variables']['node_shared'] = b(options.shared)
  node_module_version = getmoduleversion.get_version()

  if options.dest_os == 'android':
    shlib_suffix = "so"
  else:
    shlib_suffix = 'so.%s'
    if sys.platform == 'darwin':
      shlib_suffix = '%s.dylib'
    elif sys.platform.startswith('aix'):
      shlib_suffix = '%s.a'
    else:
      shlib_suffix = 'so.%s'
    shlib_suffix %= node_module_version

  o['variables']['node_module_version'] = int(node_module_version)
  o['variables']['shlib_suffix'] = shlib_suffix

  if options.linked_module:
    o['variables']['library_files'] = options.linked_module

  o['variables']['asan'] = int(options.enable_asan or 0)

  if options.coverage:
    o['variables']['coverage'] = 'true'
  else:
    o['variables']['coverage'] = 'false'

  if options.shared:
    o['variables']['node_target_type'] = 'shared_library'
  elif options.enable_static:
    o['variables']['node_target_type'] = 'static_library'
  else:
    o['variables']['node_target_type'] = 'executable'

def configure_napi(output):
  version = getnapibuildversion.get_napi_version()
  output['variables']['napi_build_version'] = version

def configure_library(lib, output):
  shared_lib = 'shared_' + lib
  output['variables']['node_' + shared_lib] = b(getattr(options, shared_lib))

  if getattr(options, shared_lib):
    (pkg_libs, pkg_cflags, pkg_libpath, pkg_modversion) = pkg_config(lib)

    if options.__dict__[shared_lib + '_includes']:
      output['include_dirs'] += [options.__dict__[shared_lib + '_includes']]
    elif pkg_cflags:
      stripped_flags = [flag.strip() for flag in pkg_cflags.split('-I')]
      output['include_dirs'] += [flag for flag in stripped_flags if flag]

    # libpath needs to be provided ahead libraries
    if options.__dict__[shared_lib + '_libpath']:
      if flavor == 'win':
        if 'msvs_settings' not in output:
          output['msvs_settings'] = { 'VCLinkerTool': { 'AdditionalOptions': [] } }
        output['msvs_settings']['VCLinkerTool']['AdditionalOptions'] += [
          '/LIBPATH:%s' % options.__dict__[shared_lib + '_libpath']]
      else:
        output['libraries'] += [
            '-L%s' % options.__dict__[shared_lib + '_libpath']]
    elif pkg_libpath:
      output['libraries'] += [pkg_libpath]

    default_libs = getattr(options, shared_lib + '_libname')
    default_libs = ['-l{0}'.format(l) for l in default_libs.split(',')]

    if default_libs:
      output['libraries'] += default_libs
    elif pkg_libs:
      output['libraries'] += pkg_libs.split()


def configure_v8(o):
  o['variables']['v8_enable_gdbjit'] = 1 if options.gdb else 0
  o['variables']['v8_no_strict_aliasing'] = 1  # Work around compiler bugs.
  o['variables']['v8_optimized_debug'] = 0 if options.v8_non_optimized_debug else 1
  o['variables']['v8_random_seed'] = 0  # Use a random seed for hash tables.
  o['variables']['v8_promise_internal_field_count'] = 1 # Add internal field to promises for async hooks.
  o['variables']['v8_use_siphash'] = 0 if options.without_siphash else 1
  o['variables']['v8_use_snapshot'] = 0 if options.without_snapshot else 1
  o['variables']['v8_trace_maps'] = 1 if options.trace_maps else 0
  o['variables']['node_use_v8_platform'] = b(not options.without_v8_platform)
  o['variables']['node_use_bundled_v8'] = b(not options.without_bundled_v8)
  o['variables']['force_dynamic_crt'] = 1 if options.shared else 0
  o['variables']['node_enable_d8'] = b(options.enable_d8)
  if options.enable_d8:
    o['variables']['test_isolation_mode'] = 'noop'  # Needed by d8.gyp.
  if options.without_bundled_v8 and options.enable_d8:
    raise Exception('--enable-d8 is incompatible with --without-bundled-v8.')
  if options.without_bundled_v8 and options.build_v8_with_gn:
    raise Exception(
        '--build-v8-with-gn is incompatible with --without-bundled-v8.')
  if options.build_v8_with_gn:
    v8_path = os.path.join('deps', 'v8')
    print('Fetching dependencies to build V8 with GN')
    options.build_v8_with_gn = FetchDeps(v8_path)
  o['variables']['build_v8_with_gn'] = b(options.build_v8_with_gn)


def configure_openssl(o):
  variables = o['variables']
  variables['node_use_openssl'] = b(not options.without_ssl)
  variables['node_shared_openssl'] = b(options.shared_openssl)
  variables['openssl_is_fips'] = b(options.openssl_is_fips)
  variables['openssl_fips'] = ''

  if options.openssl_no_asm:
    variables['openssl_no_asm'] = 1

  if options.without_ssl:
    def without_ssl_error(option):
      error('--without-ssl is incompatible with %s' % option)
    if options.shared_openssl:
      without_ssl_error('--shared-openssl')
    if options.openssl_no_asm:
      without_ssl_error('--openssl-no-asm')
    if options.openssl_fips:
      without_ssl_error('--openssl-fips')
    return

  if options.use_openssl_ca_store:
    o['defines'] += ['NODE_OPENSSL_CERT_STORE']
  if options.openssl_system_ca_path:
    variables['openssl_system_ca_path'] = options.openssl_system_ca_path
  variables['node_without_node_options'] = b(options.without_node_options)
  if options.without_node_options:
      o['defines'] += ['NODE_WITHOUT_NODE_OPTIONS']

  if not options.shared_openssl and not options.openssl_no_asm:
    is_x86 = 'x64' in variables['target_arch'] or 'ia32' in variables['target_arch']

    # supported asm compiler for AVX2. See https://github.com/openssl/openssl/
    # blob/OpenSSL_1_1_0-stable/crypto/modes/asm/aesni-gcm-x86_64.pl#L52-L69
    openssl110_asm_supported = \
      ('gas_version' in variables and float(variables['gas_version']) >= 2.23) or \
      ('xcode_version' in variables and float(variables['xcode_version']) >= 5.0) or \
      ('llvm_version' in variables and float(variables['llvm_version']) >= 3.3) or \
      ('nasm_version' in variables and float(variables['nasm_version']) >= 2.10)

    if is_x86 and not openssl110_asm_supported:
      error('''Did not find a new enough assembler, install one or build with
       --openssl-no-asm.
       Please refer to BUILDING.md''')

  elif options.openssl_no_asm:
    warn('''--openssl-no-asm will result in binaries that do not take advantage
         of modern CPU cryptographic instructions and will therefore be slower.
         Please refer to BUILDING.md''')

  if options.openssl_no_asm and options.shared_openssl:
    error('--openssl-no-asm is incompatible with --shared-openssl')

  if options.openssl_fips or options.openssl_fips == '':
     error('FIPS is not supported in this version of Node.js')

  configure_library('openssl', o)


def configure_static(o):
  if options.fully_static or options.partly_static:
    if flavor == 'mac':
      warn("Generation of static executable will not work on OSX "
            "when using the default compilation environment")
      return

    if options.fully_static:
      o['libraries'] += ['-static']
    elif options.partly_static:
      o['libraries'] += ['-static-libgcc', '-static-libstdc++']
      if options.enable_asan:
        o['libraries'] += ['-static-libasan']


def write(filename, data):
  print_verbose('creating %s' % filename)
  with open(filename, 'w+') as f:
    f.write(data)

do_not_edit = '# Do not edit. Generated by the configure script.\n'

def glob_to_var(dir_base, dir_sub, patch_dir):
  list = []
  dir_all = '%s/%s' % (dir_base, dir_sub)
  files = os.walk(dir_all)
  for ent in files:
    (path, dirs, files) = ent
    for file in files:
      if file.endswith('.cpp') or file.endswith('.c') or file.endswith('.h'):
        # srcfile uses "slash" as dir separator as its output is consumed by gyp
        srcfile = '%s/%s' % (dir_sub, file)
        if patch_dir:
          patchfile = '%s/%s/%s' % (dir_base, patch_dir, file)
          if os.path.isfile(patchfile):
            srcfile = '%s/%s' % (patch_dir, file)
            info('Using floating patch "%s" from "%s"' % (patchfile, dir_base))
        list.append(srcfile)
    break
  return list

def configure_intl(o):
  def icu_download(path):
    depFile = 'tools/icu/current_ver.dep';
    with open(depFile) as f:
      icus = json.load(f)
    # download ICU, if needed
    if not os.access(options.download_path, os.W_OK):
      error('''Cannot write to desired download path.
        Either create it or verify permissions.''')
    attemptdownload = nodedownload.candownload(auto_downloads, "icu")
    for icu in icus:
      url = icu['url']
      (expectHash, hashAlgo, allAlgos) = nodedownload.findHash(icu)
      if not expectHash:
        error('''Could not find a hash to verify ICU download.
          %s may be incorrect.
          For the entry %s,
          Expected one of these keys: %s''' % (depFile, url, ' '.join(allAlgos)))
      local = url.split('/')[-1]
      targetfile = os.path.join(options.download_path, local)
      if not os.path.isfile(targetfile):
        if attemptdownload:
          nodedownload.retrievefile(url, targetfile)
      else:
        print('Re-using existing %s' % targetfile)
      if os.path.isfile(targetfile):
        print('Checking file integrity with %s:\r' % hashAlgo)
        gotHash = nodedownload.checkHash(targetfile, hashAlgo)
        print('%s:      %s  %s' % (hashAlgo, gotHash, targetfile))
        if (expectHash == gotHash):
          return targetfile
        else:
          warn('Expected: %s      *MISMATCH*' % expectHash)
          warn('\n ** Corrupted ZIP? Delete %s to retry download.\n' % targetfile)
    return None
  icu_config = {
    'variables': {}
  }
  icu_config_name = 'icu_config.gypi'
  def write_config(data, name):
    return

  # write an empty file to start with
  write(icu_config_name, do_not_edit +
        pprint.pformat(icu_config, indent=2) + '\n')

  # always set icu_small, node.gyp depends on it being defined.
  o['variables']['icu_small'] = b(False)

  with_intl = options.with_intl
  with_icu_source = options.with_icu_source
  have_icu_path = bool(options.with_icu_path)
  if have_icu_path and with_intl != 'none':
    error('Cannot specify both --with-icu-path and --with-intl')
  elif have_icu_path:
    # Chromium .gyp mode: --with-icu-path
    o['variables']['v8_enable_i18n_support'] = 1
    # use the .gyp given
    o['variables']['icu_gyp_path'] = options.with_icu_path
    return
  # --with-intl=<with_intl>
  # set the default
  if with_intl in (None, 'none'):
    o['variables']['v8_enable_i18n_support'] = 0
    return  # no Intl
  elif with_intl == 'small-icu':
    # small ICU (English only)
    o['variables']['v8_enable_i18n_support'] = 1
    o['variables']['icu_small'] = b(True)
    locs = set(options.with_icu_locales.split(','))
    locs.add('root')  # must have root
    o['variables']['icu_locales'] = string.join(locs,',')
    # We will check a bit later if we can use the canned deps/icu-small
  elif with_intl == 'full-icu':
    # full ICU
    o['variables']['v8_enable_i18n_support'] = 1
  elif with_intl == 'system-icu':
    # ICU from pkg-config.
    o['variables']['v8_enable_i18n_support'] = 1
    pkgicu = pkg_config('icu-i18n')
    if not pkgicu[0]:
      error('''Could not load pkg-config data for "icu-i18n".
       See above errors or the README.md.''')
    (libs, cflags, libpath, icuversion) = pkgicu
    icu_ver_major = icuversion.split('.')[0]
    o['variables']['icu_ver_major'] = icu_ver_major
    if int(icu_ver_major) < icu_versions['minimum_icu']:
      error('icu4c v%s is too old, v%d.x or later is required.' %
            (icuversion, icu_versions['minimum_icu']))
    # libpath provides linker path which may contain spaces
    if libpath:
      o['libraries'] += [libpath]
    # safe to split, cannot contain spaces
    o['libraries'] += libs.split()
    if cflags:
      stripped_flags = [flag.strip() for flag in cflags.split('-I')]
      o['include_dirs'] += [flag for flag in stripped_flags if flag]
    # use the "system" .gyp
    o['variables']['icu_gyp_path'] = 'tools/icu/icu-system.gyp'
    return

  # this is just the 'deps' dir. Used for unpacking.
  icu_parent_path = 'deps'

  # The full path to the ICU source directory. Should not include './'.
  icu_full_path = 'deps/icu'

  # icu-tmp is used to download and unpack the ICU tarball.
  icu_tmp_path = os.path.join(icu_parent_path, 'icu-tmp')

  # canned ICU. see tools/icu/README.md to update.
  canned_icu_dir = 'deps/icu-small'

  # We can use 'deps/icu-small' - pre-canned ICU *iff*
  # - with_intl == small-icu (the default!)
  # - with_icu_locales == 'root,en' (the default!)
  # - deps/icu-small exists!
  # - with_icu_source is unset (i.e. no other ICU was specified)
  # (Note that this is the *DEFAULT CASE*.)
  #
  # This is *roughly* equivalent to
  # $ configure --with-intl=small-icu --with-icu-source=deps/icu-small
  # .. Except that we avoid copying icu-small over to deps/icu.
  # In this default case, deps/icu is ignored, although make clean will
  # still harmlessly remove deps/icu.

  # are we using default locales?
  using_default_locales = ( options.with_icu_locales == icu_default_locales )

  # make sure the canned ICU really exists
  canned_icu_available = os.path.isdir(canned_icu_dir)

  if (o['variables']['icu_small'] == b(True)) and using_default_locales and (not with_icu_source) and canned_icu_available:
    # OK- we can use the canned ICU.
    icu_config['variables']['icu_small_canned'] = 1
    icu_full_path = canned_icu_dir

  # --with-icu-source processing
  # now, check that they didn't pass --with-icu-source=deps/icu
  elif with_icu_source and os.path.abspath(icu_full_path) == os.path.abspath(with_icu_source):
    warn('Ignoring redundant --with-icu-source=%s' % with_icu_source)
    with_icu_source = None
  # if with_icu_source is still set, try to use it.
  if with_icu_source:
    if os.path.isdir(icu_full_path):
      print('Deleting old ICU source: %s' % icu_full_path)
      shutil.rmtree(icu_full_path)
    # now, what path was given?
    if os.path.isdir(with_icu_source):
      # it's a path. Copy it.
      print('%s -> %s' % (with_icu_source, icu_full_path))
      shutil.copytree(with_icu_source, icu_full_path)
    else:
      # could be file or URL.
      # Set up temporary area
      if os.path.isdir(icu_tmp_path):
        shutil.rmtree(icu_tmp_path)
      os.mkdir(icu_tmp_path)
      icu_tarball = None
      if os.path.isfile(with_icu_source):
        # it's a file. Try to unpack it.
        icu_tarball = with_icu_source
      else:
        # Can we download it?
        local = os.path.join(icu_tmp_path, with_icu_source.split('/')[-1])  # local part
        icu_tarball = nodedownload.retrievefile(with_icu_source, local)
      # continue with "icu_tarball"
      nodedownload.unpack(icu_tarball, icu_tmp_path)
      # Did it unpack correctly? Should contain 'icu'
      tmp_icu = os.path.join(icu_tmp_path, 'icu')
      if os.path.isdir(tmp_icu):
        os.rename(tmp_icu, icu_full_path)
        shutil.rmtree(icu_tmp_path)
      else:
        shutil.rmtree(icu_tmp_path)
        error('--with-icu-source=%s did not result in an "icu" dir.' % \
               with_icu_source)

  # ICU mode. (icu-generic.gyp)
  o['variables']['icu_gyp_path'] = 'tools/icu/icu-generic.gyp'
  # ICU source dir relative to tools/icu (for .gyp file)
  o['variables']['icu_path'] = icu_full_path
  if not os.path.isdir(icu_full_path):
    # can we download (or find) a zipfile?
    localzip = icu_download(icu_full_path)
    if localzip:
      nodedownload.unpack(localzip, icu_parent_path)
    else:
      warn('* ECMA-402 (Intl) support didn\'t find ICU in %s..' % icu_full_path)
  if not os.path.isdir(icu_full_path):
    error('''Cannot build Intl without ICU in %s.
       Fix, or disable with "--with-intl=none"''' % icu_full_path)
  else:
    print_verbose('* Using ICU in %s' % icu_full_path)
  # Now, what version of ICU is it? We just need the "major", such as 54.
  # uvernum.h contains it as a #define.
  uvernum_h = os.path.join(icu_full_path, 'source/common/unicode/uvernum.h')
  if not os.path.isfile(uvernum_h):
    error('Could not load %s - is ICU installed?' % uvernum_h)
  icu_ver_major = None
  matchVerExp = r'^\s*#define\s+U_ICU_VERSION_SHORT\s+"([^"]*)".*'
  match_version = re.compile(matchVerExp)
  for line in open(uvernum_h).readlines():
    m = match_version.match(line)
    if m:
      icu_ver_major = m.group(1)
  if not icu_ver_major:
    error('Could not read U_ICU_VERSION_SHORT version from %s' % uvernum_h)
  elif int(icu_ver_major) < icu_versions['minimum_icu']:
    error('icu4c v%s.x is too old, v%d.x or later is required.' %
          (icu_ver_major, icu_versions['minimum_icu']))
  icu_endianness = sys.byteorder[0];
  o['variables']['icu_ver_major'] = icu_ver_major
  o['variables']['icu_endianness'] = icu_endianness
  icu_data_file_l = 'icudt%s%s.dat' % (icu_ver_major, 'l')
  icu_data_file = 'icudt%s%s.dat' % (icu_ver_major, icu_endianness)
  # relative to configure
  icu_data_path = os.path.join(icu_full_path,
                               'source/data/in',
                               icu_data_file_l)
  # relative to dep..
  icu_data_in = os.path.join('..','..', icu_full_path, 'source/data/in', icu_data_file_l)
  if not os.path.isfile(icu_data_path) and icu_endianness != 'l':
    # use host endianness
    icu_data_path = os.path.join(icu_full_path,
                                 'source/data/in',
                                 icu_data_file)
    # relative to dep..
    icu_data_in = os.path.join('..', icu_full_path, 'source/data/in',
                               icu_data_file)
  # this is the input '.dat' file to use .. icudt*.dat
  # may be little-endian if from a icu-project.org tarball
  o['variables']['icu_data_in'] = icu_data_in
  if not os.path.isfile(icu_data_path):
    # .. and we're not about to build it from .gyp!
    error('''ICU prebuilt data file %s does not exist.
       See the README.md.''' % icu_data_path)
  # map from variable name to subdirs
  icu_src = {
    'stubdata': 'stubdata',
    'common': 'common',
    'i18n': 'i18n',
    'tools': 'tools/toolutil',
    'genccode': 'tools/genccode',
    'genrb': 'tools/genrb',
    'icupkg': 'tools/icupkg',
  }
  # this creates a variable icu_src_XXX for each of the subdirs
  # with a list of the src files to use
  for i in icu_src:
    var  = 'icu_src_%s' % i
    path = '../../%s/source/%s' % (icu_full_path, icu_src[i])
    icu_config['variables'][var] = glob_to_var('tools/icu', path, 'patches/%s/source/%s' % (icu_ver_major, icu_src[i]) )
  # write updated icu_config.gypi with a bunch of paths
  write(icu_config_name, do_not_edit +
        pprint.pformat(icu_config, indent=2) + '\n')
  return  # end of configure_intl

def configure_inspector(o):
  disable_inspector = (options.without_inspector or
                       options.with_intl in (None, 'none') or
                       options.without_ssl)
  o['variables']['v8_enable_inspector'] = 0 if disable_inspector else 1


def make_bin_override():
  if sys.platform == 'win32':
    raise Exception('make_bin_override should not be called on win32.')
  # If the system python is not the python we are running (which should be
  # python 2), then create a directory with a symlink called `python` to our
  # sys.executable. This directory will be prefixed to the PATH, so that
  # other tools that shell out to `python` will use the appropriate python

  which_python = which('python')
  if (which_python and
      os.path.realpath(which_python) == os.path.realpath(sys.executable)):
    return

  bin_override = os.path.abspath('out/tools/bin')
  try:
    os.makedirs(bin_override)
  except OSError as e:
    if e.errno != errno.EEXIST: raise e

  python_link = os.path.join(bin_override, 'python')
  try:
    os.unlink(python_link)
  except OSError as e:
    if e.errno != errno.ENOENT: raise e
  os.symlink(sys.executable, python_link)

  # We need to set the environment right now so that when gyp (in run_gyp)
  # shells out, it finds the right python (specifically at
  # https://github.com/nodejs/node/blob/d82e107/deps/v8/gypfiles/toolchain.gypi#L43)
  os.environ['PATH'] = bin_override + ':' + os.environ['PATH']

  return bin_override

output = {
  'variables': {},
  'include_dirs': [],
  'libraries': [],
  'defines': [],
  'cflags': [],
}

# Print a warning when the compiler is too old.
check_compiler(output)

# determine the "flavor" (operating system) we're building for,
# leveraging gyp's GetFlavor function
flavor_params = {}
if (options.dest_os):
  flavor_params['flavor'] = options.dest_os
flavor = GetFlavor(flavor_params)

configure_node(output)
configure_napi(output)
configure_library('zlib', output)
configure_library('http_parser', output)
configure_library('libuv', output)
configure_library('libcares', output)
configure_library('nghttp2', output)
# stay backwards compatible with shared cares builds
output['variables']['node_shared_cares'] = \
    output['variables'].pop('node_shared_libcares')
configure_v8(output)
configure_openssl(output)
configure_intl(output)
configure_static(output)
configure_inspector(output)

# variables should be a root level element,
# move everything else to target_defaults
variables = output['variables']
del output['variables']
variables['is_debug'] = B(options.debug)

# make_global_settings for special FIPS linking
# should not be used to compile modules in node-gyp
config_fips = { 'make_global_settings' : [] }
if 'make_fips_settings' in output:
  config_fips['make_global_settings'] = output['make_fips_settings']
  del output['make_fips_settings']
  write('config_fips.gypi', do_not_edit +
        pprint.pformat(config_fips, indent=2) + '\n')

# make_global_settings should be a root level element too
if 'make_global_settings' in output:
  make_global_settings = output['make_global_settings']
  del output['make_global_settings']
else:
  make_global_settings = False

output = {
  'variables': variables,
  'target_defaults': output,
}
if make_global_settings:
  output['make_global_settings'] = make_global_settings

print_verbose(output)

write('config.gypi', do_not_edit +
      pprint.pformat(output, indent=2) + '\n')

write('config.status', '#!/bin/sh\nset -x\nexec ./configure ' +
      ' '.join([pipes.quote(arg) for arg in original_argv]) + '\n')
os.chmod('config.status', 0o775)


config = {
  'BUILDTYPE': 'Debug' if options.debug else 'Release',
  'NODE_TARGET_TYPE': variables['node_target_type'],
}

# Not needed for trivial case. Useless when it's a win32 path.
if sys.executable != 'python' and ':\\' not in sys.executable:
  config['PYTHON'] = sys.executable

if options.prefix:
  config['PREFIX'] = options.prefix

if options.use_ninja:
  config['BUILD_WITH'] = 'ninja'

config_lines = ['='.join((k,v)) for k,v in config.items()]
# Add a blank string to get a blank line at the end.
config_lines += ['']
config_str = '\n'.join(config_lines)

# On Windows there's no reason to search for a different python binary.
bin_override = None if sys.platform == 'win32' else make_bin_override()
if bin_override:
  config_str = 'export PATH:=' + bin_override + ':$(PATH)\n' + config_str

write('config.mk', do_not_edit + config_str)



gyp_args = ['--no-parallel', '-Dconfiguring_node=1']

if options.use_ninja:
  gyp_args += ['-f', 'ninja']
elif flavor == 'win' and sys.platform != 'msys':
  gyp_args += ['-f', 'msvs', '-G', 'msvs_version=auto']
else:
  gyp_args += ['-f', 'make-' + flavor]

if options.compile_commands_json:
  gyp_args += ['-f', 'compile_commands_json']

# pass the leftover positional arguments to GYP
gyp_args += args

if warn.warned and not options.verbose:
  warn('warnings were emitted in the configure phase')

print_verbose("running: \n    " + " ".join(['python', 'tools/gyp_node.py'] + gyp_args))
run_gyp(gyp_args)
info('configure completed successfully')

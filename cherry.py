#!/usr/bin/python
#
# A simple tool to build JavaScript scripts (with Closure Templates) and LESS
# stylesheets for a web project.
#
# (c) Vincent Simonet, 2015.  All rights reserved.
# Version: 2015-11-04

import argparse
import collections
import os
import os.path
import shutil
import subprocess
import sys
import urllib2

# TODO: Single CSS file?
# TODO: Automatic GIT ignore

_DESCRIPTION="""Compile JavaScript and CSS files for a web application.

Typical usages:
  cherry.py: Compile the application.
  cherry.py --dev: Compile the application in development mode.
  cherry.py --clean: Delete generated files.
"""


# *************************************************************************
# Utility functions

def _get_relative_sub_path(path, start):
  relpath = os.path.relpath(path, start)
  if os.path.commonprefix([relpath, '..']):
    return None
  else:
    return relpath


def _is_url(path):
  return '://' in path


# *************************************************************************
# Encoding functions

def _encode(s):
  """If s is a string, return s.  If s is an unicode, return
     its encoding in utf-8.
  """
  if type(s) == unicode: return s.encode("utf-8")
  return str(s)


def _escape_js(s):
  return s  # TODO


def _escape_less(s):
  return s  # TODO


_JS_RUNTIME_START = """
(function(global) {
var cherry = {};

cherry.rewrite_path = function(path) {
  if (!cherry.is_absolute(path))
    path = cherry.BASE_URL + path;
  // TODO: Escape path
  return path;
};

cherry.include_js = function(path) {
  global.document.write(
    '<script type="text/javascript" src="' + cherry.rewrite_path(path) +
      '"></' + 'script>');
};

cherry.include_css = function(path) {
  global.document.write(
    '<link rel="stylesheet" type="text/css" href="' + cherry.rewrite_path(path) +
      '">');
};

cherry.include_less = function(path) {
  global.document.write(
    '<link rel="stylesheet/less" type="text/css" href="' + 
      cherry.rewrite_path(path) + '">');
};

cherry.is_absolute = function(url) {
  return url.startsWith('/') || url.indexOf('://') > 0;
}

cherry.set_base_url = function(name) {
  var elements = global.document.getElementsByTagName('script');
  for (var i = 0; i < elements.length; ++i) {
    var element = elements[i];
    if (element.src.endsWith('/' + name)) {
      cherry.BASE_URL = element.src.substring(0, element.src.length - 13);
      return;
    }
  }
  global.console.error('Cannot find base URL for strawberry.');
  cherry.BASE_URL = '';
};
"""

_JS_RUNTIME_END = """
})(this);
"""

# *************************************************************************
# Run external commands


class RunError(Exception):

  def __init__(self, __strerror, exn=None, **attr):
    self.strerror = __strerror
    self.attr = attr

  def __str__(self):
    attritems = self.attr.items()
    attritems.sort()
    return ("\n".join ([self.strerror] +
                       ['  ' + name + ": " + _encode(value)
                        for name, value in attritems]))


def run(command, arguments=[], inputs=[]):
  try:
    process = subprocess.Popen(
      [command] + arguments,
      bufsize=1,  # Line buffered
      stdin=subprocess.PIPE,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE)
    for i in inputs:
      process.stdin.write(i)
    process.stdin.close()
    stdout = process.stdout.readlines()
    stderr = process.stderr.readlines()
    retcode = process.wait()
    if retcode != 0:
      raise RunError("The command '" + command + "' failed",
                     command=command,
                     arguments=arguments,
                     stderr="\n".join(stderr),
                     retcode=retcode)
    return stdout
  except OSError as e:
    raise RunError("The command '" + command + "' cannot be run.", e,
                   command=command,
                   arguments=arguments)



# *************************************************************************
# Files

class File(object):
  """Interface for source files."""

  def __init__(self):
    pass

  def get_type(self):
    raise NotImplementedError

  def get_path(self):
    raise NotImplementedError

  def read(self):
    raise NotImplementedError


class FSFile(File):
  """A source file stored on the file system."""

  def __init__(self, path):
    File.__init__(self)
    self._path = path
    self._contents = None

  def get_type(self):
    _, ext = os.path.splitext(self._path)
    return ext[1:]

  def get_path(self):
    return self._path

  def read(self):
    if self._contents is None:
      with open(self._path, 'r') as f:
        self._contents = f.read()
    return self._contents


class UrlFile(File):
  """A source file stored remotely."""

  def __init__(self, path):
    File.__init__(self)
    self._path = path
    self._contents = None

  def get_type(self):
    _, ext = os.path.splitext(self._path)
    return ext[1:]

  def get_path(self):
    return self._path

  def read(self):
    if self._contents is None:
      self._contents = urllib2.urlopen(self._path).read()
    return self._contents

 
class MFile(File):
  """A source file stored in memory."""

  def __init__(self, t, contents):
    File.__init__(self)
    self._type = t
    self._contents = contents

  def get_type(self):
    return self._type

  def get_path(self):
    return None

  def read(self):
    return self._contents


class IncludeFile(MFile):

  def __init__(self, t, path):
    MFile.__init__(self, 'js', 'cherry.include_%s("%s");\n' % (
      t, _escape_js(path)))


# *************************************************************************
# Handler


_HANDLER_CLASSES = []
def register_handler(handler_class):
  _HANDLER_CLASSES.append(handler_class)


class Param(object):
  CLEAN = 'clean'
  DEV = 'dev'
  OUTPUT = 'output'
  PRETTY = 'pretty'
  LOG_LEVEL = 'log_level'


class LogLevel(object):
  QUITE = 0
  DEFAULT = 1
  VERBOSE = 2


class Handler(object):

  def __init__(self, output, parameters):
    self._parameters = parameters
    self._output = output
    self._clean = parameters.get(Param.CLEAN, False)
    self._dev = parameters.get(Param.DEV, False)
    self._pretty = parameters.get(Param.PRETTY, False)
    self._log_level = parameters.get(Param.LOG_LEVEL, LogLevel.DEFAULT)

  def handle(self, zfile, stack):
    raise NotImplementedError

  def finalize(self):
    raise NotImplementedError

  def _get_rel_path_internal(self, path, create):
    outdir = os.path.dirname(self._output)
    rel_path = _get_relative_sub_path(path, outdir)
    if rel_path:
      return False, rel_path
    else:
      if create:
        name, ext = os.path.splitext(os.path.basename(path))
        filename = name + '.dev' + ext
        shutil.copyfile(path, os.path.join(outdir, filename))
      return True, filename

  def _get_sub_path(self, path):
    _, sub_path = self._get_rel_path_internal(path, True)
    return sub_path

  def _clean_sub_path(self, path):
    copy, sub_path = self._get_rel_path_internal(path, False)
    if copy:
      self._remove_if_exists(sub_path)

  def _log(self, arg1, arg2=None):
    if arg2 is None:
      self._log(LogLevel.DEFAULT, arg1)
    else:
      level = arg1
      message = arg2
      if level <= self._log_level:
        print message

  def _run(self, command, arguments=[], inputs=[]):
    self._log(LogLevel.VERBOSE,
              'Running command: %s %s' % (command, ' '.join(arguments)))
    run(command, arguments, inputs)

  def _remove_if_exists(self, path):
    if os.path.isfile(path):
      self._log(LogLevel.VERBOSE, 'Removing file: %s' % path)
      os.remove(path)


class JavaScriptHandler(Handler):

  file_types = ['js']

  def __init__(self, *args, **kwargs):
    Handler.__init__(self, *args, **kwargs)
    self._files = []

  def handle(self, zfile, statck):
    self._files.append(zfile)

  def finalize(self):
    out_path = self._output + '.js'
    if self._clean:
      self._remove_if_exists(out_path)
    elif self._dev:
      self._log('Generating: %s' % out_path)
      with open(out_path, 'w') as out:
        out.write(_JS_RUNTIME_START)
        out.write('cherry.set_base_url("%s");\n' %
                  _escape_js(os.path.basename(out_path)))
        for zfile in self._files:
          path = zfile.get_path()
          if path is None:
            out.write(zfile.read())
          else:
            out.write('cherry.include_js("%s");\n' % _escape_js(path))
        out.write(_JS_RUNTIME_END)
    else:
      if self._files:
        self._log('Minifying JavaScript: %s' % out_path)
        options = ['--output', out_path]
        if self._pretty:
          options.append('--beautify')
        self._run(self._parameters.get('uglifyjs', 'uglifyjs'),
                  options, [zfile.read() for zfile in self._files])

register_handler(JavaScriptHandler)


class SoyHandler(Handler):

  file_types = ['soy']

  def __init__(self, *args, **kwargs):
    Handler.__init__(self, *args, **kwargs)
    self._has_soy = False
    self._soy_dir = self._parameters.get('soy_dir', '/opt/soy')
    self._soyutils_path = os.path.join(self._soy_dir, 'soyutils.js')
    self._java = self._parameters.get('java', 'java')

  def handle(self, zfile, stack):
    r = []
    stack.append(self._compile(zfile))
    if not self._has_soy:
      self._has_soy = True
      stack.append(
        FSFile(os.path.join(self._get_sub_path(self._soyutils_path))))

  def _get_out_path(self):
    out_path = self._output + '.js'

  def _compile(self, zfile):
    outpath = zfile.get_path() + '.js'
    if self._clean:
      self._remove_if_exists(outpath)
      self._clean_sub_path(self._soyutils_path)
    else:
      self._log('Compiling Closure Templates: %s' % zfile.get_path())
      self._run(self._java, [
        '-jar',
        os.path.join(self._soy_dir, 'SoyToJsSrcCompiler.jar'),
        '--codeStyle', 'stringbuilder',
        '--outputPathFormat',
        '{INPUT_DIRECTORY}/{INPUT_FILE_NAME}.js',
        zfile.get_path()
      ], [])
    return FSFile(outpath)

  def finalize(self):
    pass

register_handler(SoyHandler)


class CssHandler(Handler):

  file_types = ['css', 'less']
  _LESS_JS_RUNTIME = '/usr/share/javascript/less/less.min.js'

  def __init__(self, *args, **kwargs):
    Handler.__init__(self, *args, **kwargs)
    self._files = []
    self._has_less = False
    self._less_js = self._parameters.get('less.js', self._LESS_JS_RUNTIME)

  def handle(self, zfile, stack):
    if zfile.get_type() == 'less' and not self._has_less:
      self._has_less = True
      if self._dev:
        stack.insert(0, FSFile(self._get_sub_path(self._less_js)))
    if self._dev:
      stack.append(IncludeFile(zfile.get_type(), zfile.get_path()))
    else:
      self._files.append(zfile)

  def finalize(self):
    out_path = self._output + '.css'
    if self._clean:
      self._remove_if_exists(out_path)
      if self._has_less:
        self._clean_sub_path(self._less_js)
    elif self._dev:
      self._remove_if_exists(out_path)
    else:
      if self._files:
        inputs = ['@import "%s";' % zfile.get_path()
                  for zfile in self._files]
        self._log('Compiling CSS stylesheet: %s' % out_path)
        options = ['-', out_path]
        if self._pretty:
          options.extend(['-O0'])
        else:
          options.extend(['--compress', '-O2'])
        self._run(self._parameters.get('lessc', 'lessc'),
                  options, inputs)


register_handler(CssHandler)


class CherryHandler(Handler):

  file_types = ['cherry']

  def __init__(self, *args, **kwargs):
    Handler.__init__(self, *args, **kwargs)

  def handle(self, zfile, stack):
    base = os.path.dirname(zfile.get_path())
    result = []
    for line in reversed(zfile.read().splitlines()):
      if line and not line.startswith('#'):
        if _is_url(line):
          stack.append(UrlFile(line))
        else:
          if not os.path.isabs(line):
            line = os.path.join(base, line)
          stack.append(FSFile(line))

  def finalize(self):
    pass


register_handler(CherryHandler)


# *************************************************************************
# Class Cherry

class Cherry(object):

  def __init__(self, output, parameters):
    self._build_handlers(output, parameters)
  
  def _build_handlers(self, output, parameters):
    self._handlers = [cls(output, parameters) for cls in _HANDLER_CLASSES]
    self._handlers_dict = collections.defaultdict(list)
    for handler in self._handlers:
      for file_type in handler.file_types:
        self._handlers_dict[file_type].append(handler)

  def handle(self, cherry_file):
    # Process files
    stack = [cherry_file]
    while stack:
      zfile = stack.pop()
      for handlers in self._handlers_dict[zfile.get_type()]:
        handlers.handle(zfile, stack)
    # Finalize
    for handler in self._handlers:
      handler.finalize()


# *************************************************************************
# Main


def _is_cherry_file(path):
  _, ext = os.path.splitext(path)
  return ext == '.cherry'


def main():
  parser = argparse.ArgumentParser(description=_DESCRIPTION)
  parser.add_argument('file',
                      type=str,
                      nargs='*')
  parser.add_argument('-s', '--set',
                      action='append',
                      type=str,
                      dest='parameters_list',
                      help='Set a parameter',
                      metavar='NAME=VALUE')
  parser.add_argument('-v', '--verbose',
                      action='store',
                      type=int,
                      dest='log_level',
                      help='Log level (0=quiet, 1=default, 2=verbose)',
                      metavar='INT')
  parser.add_argument('-d', '--dev',
                      action='store_true',
                      dest='dev',
                      help='Enable development mode',
                      default=False)
  parser.add_argument('-o', '--output',
                      action='store',
                      type=str,
                      dest='output',
                      help='Set the output base name',
                      metavar='NAME')
  parser.add_argument('--clean',
                      action='store_true',
                      dest='clean',
                      help='Delete generated files instead of creating them',
                      default=False)
  parser.add_argument('-p', '--pretty',
                      action='store_true',
                      dest='pretty',
                      help='Pretty-print output',
                      default=False)
  args = parser.parse_args()
  parameters = {name: value for name, value in 
                (entry.split('=', 1) for entry in
                 (args.parameters_list or []))}
  if args.clean: parameters[Param.CLEAN] = True
  if args.dev: parameters[Param.DEV] = True
  if args.pretty: parameters[Param.PRETTY] = True
  if args.log_level: parameters[Param.LOG_LEVEL] = args.log_level
  if not args.file:
    args.file = ['.']
  if args.output and len(args) > 1:
    print >> sys.stderr, '--output cannot be used with several inputs'
    exit(1)
  try:
    for arg in args.file:
      if os.path.isdir(arg):
        paths = [path for path in os.listdir(arg) if _is_cherry_file(path)]
      else:
        paths = [arg]
      for path in paths:
        output, _ = os.path.splitext(path)
        cherry = Cherry(output, parameters)
        cherry.handle(FSFile(path))
  except RunError as e:
    print >> sys.stderr, e
    exit(1)

main()

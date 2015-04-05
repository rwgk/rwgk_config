from __future__ import division

import time
import sys, os
op = os.path

class group_args(object):

  def __init__(self, **keyword_arguments):
    self.__dict__.update(keyword_arguments)

  def __call__(self):
    return self.__dict__

def size_as_string_with_commas(sz, result_if_none="unknown"):
  if (sz is None): return result_if_none
  if (sz < 0):
    sz = -sz
    sign = "-"
  else:
    sign = ""
  result = []
  while True:
    if (sz >= 1000):
      result.insert(0, "%03d" % (sz % 1000))
      sz //= 1000
      if (sz == 0): break
    else:
      result.insert(0, "%d" % sz)
      break
  return sign + ",".join(result)

def show_string(s):
  if (s is None): return None
  if (s.find('"') < 0): return '"'+s+'"'
  if (s.find("'") < 0): return "'"+s+"'"
  return '"'+s.replace('"','\\"')+'"'

def remove_files(pattern=None, paths=None, ensure_success=True):
  assert [pattern, paths].count(None) == 1
  if (paths is None):
    import glob
    paths = glob.glob(pattern)
  for path in paths:
    if (ensure_success):
      if (op.exists(path)):
        os.remove(path)
        if (op.exists(path)):
          raise RuntimeError("Cannot remove file: %s" % show_string(path))
    else:
      if (op.isfile(path)):
        os.remove(path)

def show_sorted_by_counts(
      label_count_pairs,
      reverse=True,
      out=None,
      prefix="",
      annotations=None):
  assert annotations is None or len(annotations) == len(label_count_pairs)
  if (out is None): out = sys.stdout
  if (len(label_count_pairs) == 0): return False
  def sort_function(a, b):
    if (reverse):
      if (a[1] > b[1]): return -1
      if (a[1] < b[1]): return  1
    else:
      if (a[1] > b[1]): return -1
      if (a[1] < b[1]): return  1
    return cmp(a[0], b[0])
  if (annotations is None):
    annotations = [None]*len(label_count_pairs)
  lca = [(show_string(l), c, a)
    for (l,c),a in zip(label_count_pairs, annotations)]
  lca.sort(sort_function)
  fmt = "%%-%ds %%%dd" % (
    max([len(l) for l,c,a in lca]),
    max([len(str(lca[i][1])) for i in [0,-1]]))
  for l,c,a in lca:
    print >> out, prefix+fmt % (l,c),
    if (a is not None and len(a) > 0): print >> out, a,
    print >> out
  return True

class caller_location(object):

  def __init__(self, frames_back=0):
    f = sys._getframe(frames_back+1)
    self.file_name = f.f_code.co_filename
    self.line_number = f.f_lineno

  def __str__(self):
    return "%s(%d)" % (self.file_name, self.line_number)

def check_point(frames_back=0):
  print caller_location(frames_back=frames_back+1)
  sys.stdout.flush()

def show_stack(
      max_frames_back=None,
      frames_back=0,
      reverse=False,
      out=None,
      prefix=""):
  if (out is None): out = sys.stdout
  lines = []
  try:
    while True:
      if (max_frames_back is not None and frames_back == max_frames_back):
        break
      f = sys._getframe(frames_back+1)
      lines.append(prefix+"show_stack(%d): %s(%d) %s" % (
        frames_back, f.f_code.co_filename, f.f_lineno, f.f_code.co_name))
      frames_back += 1
  except ValueError:
    pass
  if (reverse): lines.reverse()
  if (out == "return_lines"):
    return lines
  for line in lines:
    print >> out, line

kb_exponents = {
  "KB": 1,
  "MB": 2,
  "GB": 3,
  "TB": 4,
  "PB": 5}

class proc_file_reader(object):

  def get_bytes(self, vm_key):
    if (self.proc_status is None):
      return None
    try:
      i = self.proc_status.index(vm_key)
    except ValueError:
      return None
    flds = self.proc_status[i:].split(None, 3)
    if (len(flds) < 3):
      return None
    exponent = kb_exponents.get(flds[2].upper())
    try:
      num = int(flds[1])
    except ValueError:
      return None
    return num * 1024**exponent

try:
  _proc_status = "/proc/%d/status" % os.getpid()
except AttributeError:
  _proc_status = None

class virtual_memory_info(proc_file_reader):

  have_vmpeak = False
  max_virtual_memory_size = 0
  max_resident_set_size = 0
  max_stack_size = 0

  def __init__(self):
    try:
      self.proc_status = open(_proc_status).read()
    except IOError:
      self.proc_status = None

  def virtual_memory_peak_size(self):
    result = self.get_bytes('VmPeak:')
    if (result is not None):
      virtual_memory_info.have_vmpeak = True
    virtual_memory_info.max_virtual_memory_size = max(
    virtual_memory_info.max_virtual_memory_size, result)
    return result

  def virtual_memory_size(self):
    result = self.get_bytes('VmSize:')
    virtual_memory_info.max_virtual_memory_size = max(
    virtual_memory_info.max_virtual_memory_size, result)
    return result

  def resident_set_size(self):
    result = self.get_bytes('VmRSS:')
    virtual_memory_info.max_resident_set_size = max(
    virtual_memory_info.max_resident_set_size, result)
    return result

  def stack_size(self):
    result = self.get_bytes('VmStk:')
    virtual_memory_info.max_stack_size = max(
    virtual_memory_info.max_stack_size, result)
    return result

  def update_max(self):
    if (self.proc_status is not None):
      self.virtual_memory_peak_size()
      self.virtual_memory_size()
      self.resident_set_size()
      self.stack_size()

  def show(self, out=None, prefix="", show_max=False):
    if (out is None): out = sys.stdout
    vms = size_as_string_with_commas(self.virtual_memory_size())
    rss = size_as_string_with_commas(self.resident_set_size())
    sts = size_as_string_with_commas(self.stack_size())
    fmt = "%%%ds" % max(len(vms), len(rss), len(sts))
    lvms = prefix + "Virtual memory size:"
    lrss = prefix + "Resident set size:  "
    lsts = prefix + "Stack size:         "
    if (not show_max):
      print >> out, lvms, fmt % vms
      print >> out, lrss, fmt % rss
      print >> out, lsts, fmt % sts
    else:
      self.virtual_memory_peak_size()
      vmi = virtual_memory_info
      max_vms = size_as_string_with_commas(vmi.max_virtual_memory_size)
      max_rss = size_as_string_with_commas(vmi.max_resident_set_size)
      max_sts = size_as_string_with_commas(vmi.max_stack_size)
      max_fmt = "%%%ds" % max(len(max_vms), len(max_rss), len(max_sts))
      if (vmi.have_vmpeak):
        vms_what_max = "    exact max:"
      else:
        vms_what_max = "  approx. max:"
      print >> out, lvms, fmt % vms, vms_what_max,     max_fmt % max_vms
      print >> out, lrss, fmt % rss, "  approx. max:", max_fmt % max_rss
      print >> out, lsts, fmt % sts, "  approx. max:", max_fmt % max_sts

  def show_if_available(self, out=None, prefix="", show_max=False):
    if (self.proc_status is not None):
      self.show(out=out, prefix=prefix, show_max=show_max)

  def current_max_sizes_legend(self):
    return ("Virtual memory", "Resident set", "Stack")

  def current_max_sizes(self):
    return group_args(
      virtual_memory=self.max_virtual_memory_size,
      resident_set=self.max_resident_set_size,
      stack=self.max_stack_size)

def get_imported_modules():
  result = []
  for name,module in sys.modules.items():
    if module:
      path = getattr(module, '__file__', None)
      if path:
        if path.endswith('.pyc'):
          path = path[:-1]
        result.append((os.path.getsize(path), name, path))
  result.sort()
  return result

def compare_imported_modules(list1, list2):
  pair_dict = {}
  for size,name,path in list1:
    pair_dict[name] = [(size,path), (None,None)]
  for size,name,path in list2:
    pair = pair_dict.get(name)
    if pair:
      pair[1] = (size,path)
    else:
      pair_dict[name] = [(None,None), (size,path)]
  size_name = []
  for name,pair in pair_dict.items():
    if pair[0][0]:
      size = pair[0][0]
      if pair[1][0]:
        size = max(size, pair[1][0])
    else:
      size = pair[1][0]
    size_name.append((size, name))
  size_name.sort()
  size_name.reverse()
  total_modules = [0, 0]
  total_sizes = [0, 0]
  pair_list = [(name, pair_dict[name]) for size,name in size_name]
  for name,pair in pair_list:
    sz0, nm0 = pair[0]
    sz1, nm1 = pair[1]
    print name
    print ' ', size_as_string_with_commas(sz0, result_if_none='None'), nm0
    print ' ', size_as_string_with_commas(sz1, result_if_none='None'), nm1
    print
    if sz0 is not None:
      total_modules[0] += 1
      total_sizes[0] += sz0
    if sz1 is not None:
      total_modules[1] += 1
      total_sizes[1] += sz1
  print 'number of modules:', str(total_modules)[1:-1]
  print 'sum of sizes:', ', '.join([
    size_as_string_with_commas(size, result_if_none='None')
      for size in total_sizes])
  print

def show_imported_modules(out=None):
  if out is None:
    out = sys.stdout
  imported_modules = get_imported_modules()
  size_sum = 0
  for size,name,path in imported_modules:
    print >> out, size_as_string_with_commas(size), name, path
    size_sum += size
  print >> out, 'number of modules:', len(imported_modules)
  print >> out, 'total size:', size_as_string_with_commas(size_sum)
  return imported_modules

def simple_histogram(low, high, slot_width, values, num_slots=None, allow_higher=False):
  assert high >= low
  if num_slots is None:
    num_slots = int((high - low) / slot_width) + 1
  else:
    slot_width = (high - low) / num_slots * (1+1e-6)
  print '# slot_width:', slot_width
  slots = [0] * num_slots
  for v in values:
    i = int((v - low) / slot_width)
    assert i >= 0
    if not allow_higher:
      assert i < num_slots
    else:
      if i >= num_slots:
        i = num_slots - 1
    slots[i] += 1
  return slots

decimal_digits = set('0123456789')

def mktime_from_log_timestamp(ymd, hmsm):
  if len(ymd) != 10:
    return None
  ymd = ymd.split('-')
  if len(ymd) != 3:
    return None
  hmsm = hmsm.split(':')
  if len(hmsm) == 2:
    hmsm.append('00')
  elif len(hmsm) != 3:
    return None
  values = []
  for s in ymd:
    if not set(s).issubset(decimal_digits):
      return None
    values.append(int(s))
  sm = hmsm[2].split(',')
  if len(sm) == 2:
    if len(sm[1]) != 3:
      return None
    hmsm[2] = sm[0]
    hmsm.append(sm[1])
  for s in hmsm:
    if len(s) < 2 or not set(s).issubset(decimal_digits):
      return None
    values.append(int(s))
  result = time.mktime(values[:6] + [0, 0, -1])
  if len(values) == 7:
    result += values[6] / 1000
  return result

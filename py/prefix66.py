from cStringIO import StringIO
import csv

def prefix66(mode, phone_info):
  num_changed = 0
  result = []
  for t,v in phone_info:
    assert v.strip() == v
    assert len(v.split(':')) == 1
    is66 = v.startswith('+66-')
    is0d = len(v) > 1 and v[0] == '0' and v[1] != 0 and v[1].isdigit()
    if mode == 'add66':
      assert not is66
      if is0d:
        v = '+66-' + v[1:]
        num_changed += 1
    elif mode == 'del66':
      assert not is0d
      if is66:
        v = '0' + v[4:]
        num_changed -= 1
    else:
      raise RuntimeError
    result.append((t, v))
  del phone_info[:]
  phone_info.extend(result)
  return num_changed

def run(args):
  assert len(args) == 2
  input_csv, mode = args
  assert mode in ['add66', 'del66']
  utf16 = open(input_csv, 'rb').read()
  utf8 = utf16.decode('utf-16').encode('utf-8')
  reader = csv.reader(StringIO(utf8), delimiter=',', quotechar='"')
  utf8_out = StringIO()
  writer = csv.writer(
    utf8_out, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
  header_row = reader.next()
  writer.writerow(header_row)
  def get_header_indices(col_head, col_tail):
    result = []
    for i,col in enumerate(header_row):
      if col.startswith(col_head) and col.endswith(col_tail):
        result.append(i)
    return result
  phone_type_indices = get_header_indices('Phone ', ' - Type')
  phone_value_indices = get_header_indices('Phone ', ' - Value')
  assert len(phone_type_indices) == len(phone_value_indices)
  num_changed = 0
  for row in reader:
    phone_info = []
    for i,j in zip(phone_type_indices, phone_value_indices):
      phone_info.append((row[i], row[j]))
    num_changed += prefix66(mode, phone_info)
    for clean,i,j in zip(phone_info, phone_type_indices, phone_value_indices):
      row[i], row[j] = clean
    writer.writerow(row)
  f = open('tmp.csv', 'wb')
  blob = utf8_out.getvalue().decode('utf-8').encode('utf-16')
  f.write(blob)
  print 'num_changed:', num_changed

if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])

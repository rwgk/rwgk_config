def run(args):
  assert len(args) == 0
  import rwgk_utils
  rwgk_utils.caller_location()
  rwgk_utils.check_point()
  rwgk_utils.show_stack()
  rwgk_utils.virtual_memory_info().show(show_max=True)
  print "Done."

if __name__ == '__main__':
  import sys
  run(args=sys.argv[1:])

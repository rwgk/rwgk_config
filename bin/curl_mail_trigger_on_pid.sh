#! /bin/bash
if [ $# -ne 2 ]; then
  echo "usage: $0 user@domain PID  # exactly two arguments required, $# given."
  exit 1
fi
which curl_gmail.sh > /dev/null
if [ $? -ne 0 ]; then
  echo 'FATAL: curl_gmail.sh not available.'
  exit 1
fi
email_to="$1"
pid_watch="$2"
posix_time_at0="$(/bin/date '+%s')"
etimes_at0="$(/bin/ps -p "$pid_watch" -o etimes=)"
ps_args="$(/bin/ps -p $pid_watch -o args=)"
if [ $? -ne 0 -o -z "$ps_args" ]; then
  echo "FATAL: process $pid_watch does not exist."
  exit 1
fi
/usr/bin/tail -f --pid "$pid_watch" /dev/null  # Wait for process to finish.
posix_time_done="$(/bin/date '+%s')"
process_seconds=$(( $posix_time_done - $posix_time_at0 + $etimes_at0 ))
curl_gmail.sh "$email_to" "$email_to" "Done: $process_seconds seconds: $pid_watch $ps_args"
exit 0

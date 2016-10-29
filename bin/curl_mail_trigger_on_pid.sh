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
ps_args="$(/bin/ps -p $pid_watch -o args=)"
if [ $? -ne 0 -o -z "$ps_args" ]; then
  echo "FATAL: process $pid_watch does not exist."
  exit 1
fi
while /bin/true; do
  PID="$(/bin/ps -p $pid_watch -o pid=)"
  if [ -z "$PID" ]; then
    curl_gmail.sh "$email_to" "$email_to" "Done: $pid_watch $ps_args"
    exit 0
  fi
  /bin/sleep 5
done

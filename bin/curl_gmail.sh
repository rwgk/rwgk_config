#! /bin/bash
set -e
REPLYTO="$1"
shift
MAILTO="$1"
shift
RELAYUSR=rwgkmailrelay@gmail.com
# Account created 2016-10-22.
# Access for less secure apps turned on here:
#   https://www.google.com/settings/security/lesssecureapps
RELAYKEYFILE="$HOME/.rwgkmailrelay"
# Check file permissions (grep exit code is non-zero if no match).
set +e
/bin/ls -l "$RELAYKEYFILE" | /bin/grep -q '^-r-------- '
if [ $? -ne 0 ]; then
  echo "Unsafe permissions on file $RELAYKEYFILE (chmod 400 to fix)."
  exit 1
fi
set -e
RELAYKEY="$(/bin/cat $RELAYKEYFILE)"
/usr/bin/curl --silent --url smtps://smtp.gmail.com:465 --ssl-reqd --user "${RELAYUSR}:${RELAYKEY}" --mail-from "$RELAYUSR" --mail-rcpt "$MAILTO" --upload-file - << EOT
To: $MAILTO
From: Mail Relay <$RELAYUSR>
Reply-To: $REPLYTO
Subject: $@

Message in Subject.
EOT

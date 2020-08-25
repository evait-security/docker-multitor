#!/bin/bash

if [[ -z "${TOR_INSTANCES}" ]]; then
  TOR_INSTANCE_COUNT=5
  echo "Environment variable TOR_INSTANCES not specified, defaulting to 5"
else
  TOR_INSTANCE_COUNT="${TOR_INSTANCES}"
fi

re='^[0-9]+$'
if ! [[ $TOR_INSTANCE_COUNT =~ $re ]] ; then
   echo "error: TOR_INSTANCES is not a number, defaulting to 5"
   TOR_INSTANCE_COUNT=5
fi


multitor --init $TOR_INSTANCE_COUNT --user root --socks-port 9000 --control-port 9900 --proxy privoxy --haproxy --verbose --debug > /tmp/multitor.log; tail -f /tmp/multitor.log
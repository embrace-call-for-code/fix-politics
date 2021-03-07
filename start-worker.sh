#!/bin/bash
export USE_SQLITE3='False'
export PATH=/home/vcap/.local/bin:$PATH
cd /home/vcap
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python3 get-pip.py
python3 -m pip install pipenv
pipenv install
cd /home/vcap/app
pipenv run manage.py shell

# get_api_data.py
# By Tony Pearson, IBM, 2020
#
# This is intended as an asynchronous background task
#
# You can invoke this in either "on demand" or as part of a "cron" job
#
# On Demand:
# [..] $ pipenv shell
# (cfc) $ ./stage1 get_api_data --api --state AZ --limit 10
#
# Cron Job:
# /home/yourname/Develop/legit-info/cron1 get_api_data --api --limit 10
#
# The Legiscan.com API only allows 30,000 fetches per 30-day period, and
# each legislation requires at least 2 fetches, so use the --limit keyword
#
# If you leave out the --api, the Legiscan.com API will not be invoked,
# this is useful to see the status of AZ.json and OH.json files.
#
# Debug with:  import pdb; pdb.set_trace()

import datetime as DT
import json
import re
from django.core.management.base import BaseCommand, CommandError
from cfc_app.models import Location, Hash
from cfc_app.LegiscanAPI import LegiscanAPI
from cfc_app.FOB_Storage import FOB_Storage, DSLregex
from cfc_app.views import load_default_locations
from django.conf import settings

StateForm = 'Session {} Year: {} Date: {} Size: {} bytes'
DateForm = '{} {}'

class Command(BaseCommand):
    help = ("For each state in the United States listed in cfc_app_law "
            "database table, this script will fetch the most recent "
            "legislative sessions, and create a JSON-formatted output file "
            "SS-NNNN.json where 'SS' is the two-letter state abbreviation "
            "like AZ or OH, and 'NNNN' is the four-digit session_id assigned "
            "by Legiscan.com API. The SS-NNNN.json files are stored in "
            "File/Object Storage.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fob = FOB_Storage(settings.FOB_METHOD)
        self.leg = LegiscanAPI()
        self.use_api = False
        self.list_name = None
        self.list_data = None
        self.list_pkg = None
        self.datasetlist = None
        self.fromyear = 2018
        self.frequency = 7
        return None

    def add_arguments(self, parser):
        parser.add_argument("--api", action="store_true",
                            help="Invoke Legiscan.com API")
        parser.add_argument("--state", help="Process single state: AZ, OH")
        parser.add_argument("--frequency", type=int, default=7,
                            help="Days since last DatasetList request")
        return None

    def handle(self, *args, **options):

        # If the Legiscan DatasetList is recent enough, use it,
        # otherwise, call Legiscan API to fetch a new one

        if options['api']:
            self.use_api = True
        if options['state']:
            self.state = options['state']
        if options['frequency']:
            self.frequency = options['frequency']

        self.list_data = self.recent_enough()
        # import pdb; pdb.set_trace()

        # Get the list of states from the Django database for "Location"

        try:
            usa = Location.objects.get(shortname='usa')
        except Location.DoesNotExist:
            load_default_locations()
            usa = Location.objects.get(shortname='usa')
            
        locations = Location.objects.filter(parent=usa)
        if not locations:
            load_default_locations()
            locations = Location.objects.filter(parent=usa)

        states = []
        for loc in locations:
            state = loc.shortname.upper()  # Convert state to UPPER CASE
            state_id = loc.legiscan_id

            states.append([state, state_id])
            if options['state']:
                if state != options['state']:
                    continue

            # import pdb; pdb.set_trace()
            print('Processing: {} ({})'.format(loc.desc, state))

            # Get dataset and master files, up to the --limit set
            self.fetch_dataset(state, state_id)

        # Show status of all files we expect to have now
        self.datasets_found(states)
        return None

    def recent_enough(self):
       
        now = DT.datetime.today()
        week_ago = now - DT.timedelta(days=7)
        dsl_list = self.fob.DatasetList_items()

        latest_date = DT.datetime(1911, 6, 16, 16, 20)  # Long ago in history
        latest_name = None
        for name in dsl_list:
            mo = self.fob.DatasetList_search(name)
            if mo:
                filedate = DT.datetime.strptime(mo.group(1), "%Y-%m-%d")
                if filedate > latest_date:
                    latest_date = filedate
                    latest_name = name

        self.list_name = latest_name

        # If --api is set, but file is more than a week old, get the latest
        if self.use_api and latest_date < week_ago:      
            self.list_data = self.leg.getDatasetList('Good')

            # If successful return from API, save this to a file
            if self.list_data:
                today = now.strftime("%Y-%m-%d")
                self.list_name = self.fob.gen_DatasetList_name(today)          
                self.fob.upload_text(self.list_data, self.list_name)
            else:
                print('API Failed to get DatasetList from Legiscan')

        # API failure or not called, get the item from File/Object storage
        if latest_name and (not self.list_data):
            print('Downloading: ', self.list_name)
            self.list_data = self.fob.download_text(self.list_name)

        if self.list_data:
            print('Verifying JSON contents of: ', self.list_name)
            self.list_pkg = json.loads(self.list_data)

            # Validate this is a Legiscan DatasetList file
            if 'status' in self.list_pkg:
                if self.list_pkg['status'] != 'OK':
                    raise CommandError('Status not OK: '+self.list_name)
                else:
                    if 'datasetlist' not in self.list_pkg:
                        raise CommandError('datsetlist missing')
                    else:
                        self.datasetlist = self.list_pkg['datasetlist']

        if not self.list_data:
            print('DatasetList-YYYY-MM-DD.json not found')
            if not self.use_api:
                print('Did you forget the --api parameter?')
            raise CommandError('API failure, or DatasetList not Found')

        return

    def fetch_dataset(self, state, state_id):

        for entry in self.datasetlist:
            if entry['state_id'] == state_id:
                session_id = entry['session_id']
                access_key = entry['access_key']
                session_name = self.fob.Dataset_name(state, state_id)
                if entry['year_end'] >= self.fromyear:
                    if self.use_api and self.leg.api_ok:
                        print('Fetching {}: {}'.format(state, session_id))
                        session_data = self.leg.getDataset(session_id,
                                                           access_key)
                        self.fob.upload_text(session_data, session_name)
        return None

    def datasets_found(self, states):
        for state_data in states:
            print(' ')
            state, state_id = state_data[0], state_data[1]
            found_list = self.fob.Dataset_items(state)
            for entry in self.datasetlist:
                if (entry['state_id'] == state_id
                        and entry['year_end'] >= self.fromyear):
                    
                    session_id = entry['session_id']
                    session_name = self.fob.Dataset_name(state, session_id)
                    if session_name in found_list:
                        self.show_results(session_name, entry)
                        print('Found session dataset: ', session_name)
                    else:
                        print('Item not found: ', session_name)

                    hash = Hash()
                    hash.item_name = session_name
                    hash.fob_method = settings.FOB_METHOD
                    hash.generated_date = entry['dataset_date']
                    hash.hashcode = entry['dataset_hash']
                    hash.size = entry['dataset_size']
                    hash.desc = entry['session_name']
                    hash.save()

        return None

    def show_results(self, json_name, entry):
        year_range = str(entry['year_start'])
        if year_range != entry['year_end']:
            year_range += '-' + str(entry['year_end'])

        print(StateForm.format(entry['session_id'],
                               year_range, entry['dataset_date'],
                               entry['dataset_size']))
        return None

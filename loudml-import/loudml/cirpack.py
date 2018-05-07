import pkg_resources

import ipaddress
from datetime import datetime
import dateutil.parser
import logging

import phonenumbers
from phonenumbers import geocoder
from phonenumbers import PhoneNumberType, PhoneNumberFormat, NumberParseException

from rmn_common.data_import import Parser
from .phone_rates import PhoneRates

phonenumbers.PhoneMetadata.load_all()

def _parse_number(num, local_region, rates):
    y = phonenumbers.parse(num, local_region)

    #if phonenumbers.is_possible_number(y) == False:
    #    print("Isn't possible number:", num)
    #if phonenumbers.is_valid_number(y) == False:
    #    print("Isn't valid number:", num)

    output_num = phonenumbers.format_number(
        y,
        phonenumbers.PhoneNumberFormat.E164,
    )
    region_code = geocoder.region_codes_for_country_code(y.country_code)[0]
    international = False
    mobile = False
    premium = False

    if PhoneNumberType.MOBILE == phonenumbers.number_type(y):
        mobile = True
    if PhoneNumberType.PREMIUM_RATE == phonenumbers.number_type(y):
        premium = True
    if region_code != local_region:
        international = True

    fraud_level, pricing = rates.get_fraud_level_and_rate(output_num[1:])

    return {
        'premium': premium,
        'mobile': mobile,
        'international': international,
        'phonenumber': output_num,
        'region': region_code,
        'fraud_level': fraud_level,
        'pricing': pricing,
    }


def parse_number(num, local_region, rates):
    try:
        return _parse_number(num, local_region, rates)
    except phonenumbers.phonenumberutil.NumberParseException as exn:
        logging.error("invalid number: %s", num)
        return {
            'premium': False,
            'mobile': False,
            'international': False,
            'phonenumber': num,
            'region': 'INVALID',
            'fraud_level': 'A',
            'pricing': 1,
        }

def international_nature(nature):
    return (nature == 4 or nature == 117)


class CdrParser(Parser):
    def __init__(self):
        super().__init__()
        self._rates = PhoneRates()

    def get_template(self, db_name, measurement):
        resource = pkg_resources.resource_filename(__name__, 'resources/phonedb.template')
        content = open(resource, 'rU').read()
        return content.format(db_name, measurement)

    def decode(self, row):
        cols = row.decode('utf-8').split()
        account = cols[2]
        #‘1’ = for an incoming call, ‘0’ = for an outgoing or a transited call.
        direction = int(cols[3])
        ts = dateutil.parser.parse(cols[4] + cols[5], ignoretz=True)
        total_call_duration = int(cols[8])
        # (hexadecimal) IP address of the switch generating the CDR
        ip_addr = ipaddress.ip_address(bytes.fromhex(cols[9]))

# Number Nature
#Undefined 0
#Subscriber number (national use) 1
#Unknown 2
#National number 3
#International number 4
#Network-specific number (national use) 5
#Interworking 8
#Closed user group nature 11
#Truncated number 12
#Special 115 
#Indirect national 116
#Indirect international 117
        calling_number_nature = int(cols[14])
        calling_number = cols[15]
        calling_number_international = international_nature(calling_number_nature)
        called_number_nature = int(cols[20])
        called_number = cols[21]
        called_number_international = international_nature(called_number_nature)

        calling_dict = {}
        if calling_number_international == True:
            calling_dict = parse_number("+" + calling_number, 'FR', self._rates)
            # fix wrong 336 prefixes reported as international=True
            calling_number_international = calling_dict['international']
            calling_number_region = calling_dict['region']
        else:
            calling_number_region = 'FR'

        called_dict = {}
        if called_number_international == True:
            called_dict = parse_number("+" + called_number, 'FR', self._rates)
            # fix wrong 336 prefixes reported as international=True
            called_number_international = called_dict['international']
            called_number_region = called_dict['region']
            fraud_level = called_dict['fraud_level']
            pricing = called_dict['pricing']
        else:
            called_number_region = 'FR'
            fraud_level = 'A'
            pricing = 1

        tag_dict = {
            'account': account,
        }
        row_data = {
            'direction': direction,
            'calling_number': calling_number,
            'calling_number_region': calling_number_region,
            'called_number': called_number,
            'called_number_region': called_number_region,
            'duration': total_call_duration,
            'fraud_level': fraud_level,
            'pseudo_price': pricing * total_call_duration/60,
#            'mobile': False,
            'international': called_number_international,
#            'toll_call': False,
        }
        return int(ts.timestamp()), tag_dict, row_data

    def read_csv(self, fp, encoding):
        for row in fp:
            yield self.decode(row)



from calendar import monthrange
import copy
import re
from datetime import datetime, timedelta
import logging
import math
from operator import attrgetter
from .taf import TAF


class DecodeError(Exception):
    def __init__(self, msg):
        self.strerror = msg


class Decoder(object):
    def __init__(self, taf, taf_timestamp):
        if isinstance(taf, TAF):
            self._taf = taf
            try:
                self._decode_groups(taf_timestamp)
            except ValueError:
                logging.warning('Error decoding taf: ' + taf._raw_taf)
        else:
            raise DecodeError("Argument is not a TAF parser object")

    def decode_taf(self):
        result = ""

        result += self._decode_header(self._taf.get_header()) + "\n"

        for group in self._taf.get_groups():
            if group["header"]:
                result += self._decode_group_header(group["header"]) + "\n"

            if group["wind"]:
                result += "    Wind: %s \n" % self._decode_wind(group["wind"])

            if group["visibility"]:
                result += "    Visibility: %s \n" % self._decode_visibility(group["visibility"])

            if group["clouds"]:
                result += "    Sky conditions: %s \n" % self._decode_clouds(group["clouds"])

            if group["weather"]:
                result += "    Weather: %s \n" % self._decode_weather(group["weather"])

            if group["windshear"]:
                result += "    Windshear: %s\n" % self._decode_windshear(group["windshear"])

            result += " \n"

        if self._taf.get_maintenance():
            result += self._decode_maintenance(self._taf.get_maintenance())

        return(result)

    def get_group(self, timestamp):
        # return the group that contains timestamp
        for group in self.groups:
            if group.start_time <= timestamp < group.end_time:
                return group

        if self.groups[-1].end_time == timestamp:
            return group
        #print(self.groups)
        print('[WARNING] No TAF group found for ', timestamp.isoformat(), self.groups)
        return None

    @property
    def end_time(self):
        return self.groups[-1].end_time

    @property
    def start_time(self):
        return self.groups[0].start_time

    def _extract_time(self, header, *prefixes):
        if not header:
            raise ValueError('Expecting non-empty header')
        
        for prefix in prefixes:
            day = header.get(prefix + 'date', None)
            if day:
                day = int(day)
                if day == 0:
                    logging.warning('Invalid day for taf ' + self._taf._raw_taf)
                    raise ValueError('Invalid day for taf' + self._taf._raw_taf)
                hour = int(header.get(prefix + 'hours'))
                minute = header.get(prefix + 'minutes', 0)
                if minute == '':
                    minute = 0
                minute = int(minute)

                if hour > 24: # There are occasionally data errors,
                    hour = int(header.get('valid_from_hours'))

                return day, hour, minute
        return None
        
    def _decode_timestamp(self, header, *prefixes):
        try:
            res = self._extract_time(header, *prefixes)
        except ValueError:
            return None

        if not res:
            return None

        day, hours, minutes = res
        if hours == 24:
            hours = 23
            minutes = 59

        month = self.issued_timestamp.month
        year = self.issued_timestamp.year
        if self.issued_timestamp.day > day:
            month = self.issued_timestamp.month + 1
        if month > 12:
            month = 1
            year += 1
        try:
            month, day = self._normalize_date(year, month, day)
        except ValueError:
            return None
        return datetime(year, month, day, hours, minutes)


    def _normalize_date(self, year, month, day):
        if day == 31:
            # Check if this month does not have 31 days, and change to valid date. This error occurs in the data.
            days_in_month = monthrange(year, month)[1]
            if days_in_month == 30:
                day = 1
                month += 1
        return month, day
        
    def _decode_groups(self, taf_timestamp):
        if not taf_timestamp:
            taf_timestamp = datetime.utcnow()
        month = taf_timestamp.month
        year = taf_timestamp.year
            
        taf_header = self._taf.get_header()
        day, hours, minutes = self._extract_time(taf_header, 'origin_')
        month, day = self._normalize_date(year, month, day)
        self.issued_timestamp = datetime(year, month, day, hours, minutes)

        self.groups = [TafGroup(group, taf_header, self) for group in self._taf.get_groups()]
        self._set_missing_group_times()
        self._fill_gaps()
        self._complete_group_info()
        self._remove_extraneous_groups()
        #print('Final groups:', taf_timestamp.isoformat(), self.groups)

    def _remove_extraneous_groups(self):
        """
        Remove groups that span no time. This can occur with the interplay of TEMPO and PROB groups with FM groups.
        :return:
        """
        self.groups = [x for x in self.groups if x.start_time < x.end_time]

    def _set_missing_group_times(self):
        for index, group in enumerate(self.groups):
            if not group.start_time and index > 0:
                group.start_time = self.groups[index-1].end_time

            if not group.end_time and index < len(self.groups)-1:
                group.end_time = self.groups[index+1].start_time
            elif not group.end_time and (group.type == 'FM' or group.type == 'MAIN'):
                valid_till = self._decode_timestamp(self._taf.get_header(), 'valid_till_')
                group.end_time = valid_till # set end time of last group

            if index == len(self.groups)-1 and group.end_time.minute == 59:
                group.end_time = group.end_time + timedelta(minutes=1)

    def _has_gap(self, earliertime, latertime):
        return latertime - earliertime > timedelta(minutes=5)

    def _create_basic_group(self, startime, endtime, base_group):
        newgroup = copy.copy(base_group)
        if startime.minute == 59:
            newgroup.start_time = startime + timedelta(minutes=1)
        else:
            newgroup.start_time = startime
        newgroup.end_time = endtime
        newgroup.type = base_group.type + '-EXT'
        return newgroup

    def _fill_gaps(self):
        newgroups = []
        prev_fm_group = self.groups[0]
        for i, group in enumerate(self.groups[:-1]):
            nextgroup = self.groups[i+1]
            if group.type == 'FM' or group.type == 'MAIN':
                prev_fm_group = group
            if not group.end_time:
                logging.warning('Group does not have an end time' + str(self.groups))
                group.end_time = nextgroup.start_time # TODO: investigate when this occurs
            if self._has_gap(group.end_time, nextgroup.start_time):
                newgroups.append( self._create_basic_group(group.end_time, nextgroup.start_time, prev_fm_group))
        self.groups.extend(newgroups)
        self.groups = sorted(self.groups, key=attrgetter('start_time'))

        self._fill_gap_at_end()

    def _fill_gap_at_end(self):
        # If the last group is not a FM group, extend the main group (1st group)
        valid_till = self._decode_timestamp(self._taf.get_header(), 'valid_till_')
        if self._has_gap(self.groups[-1].end_time, valid_till):
            self.groups.append( self._create_basic_group(self.groups[-1].end_time, valid_till, self.groups[0]))

    def _complete_group_info(self):
        # When PROB40, TEMPO, and BECMG are listed in the group header, this means that
        # components from the previous group are not expected to change in the group. Identify these cases,
        # and make sure each group contains complete information
        temp_keywords = ['PROB', 'TEMPO', 'BECMG']
        prev_fm_group = self.groups[0]
        for index, group in enumerate(self.groups[1:]):
            if group.header_starts_with(temp_keywords):
                group.fill_in_information(prev_fm_group)
            else:
                prev_fm_group = group

        
    def _decode_header(self, header):
        result = ""

        # Ensure it's side effect free
        _header = header

        # Type
        if _header["type"] == "AMD":
            result += "TAF amended for "
        elif _header["type"] == "COR":
            result += "TAF corrected for "
        elif _header["type"] == "RTD":
           result += "TAF related for "
        else:
            result += "TAF for "

        # Add ordinal suffix
        _header["origin_date"] = _header["origin_date"] + self._get_ordinal_suffix(_header["origin_date"])
        _header["valid_from_date"] = _header["valid_from_date"] + self._get_ordinal_suffix(_header["valid_from_date"]) 
        _header["valid_till_date" ] = _header["valid_till_date"] + self._get_ordinal_suffix(_header["valid_till_date"])

        result += ("%(icao_code)s issued %(origin_hours)s:%(origin_minutes)s UTC on the %(origin_date)s, " 
                   "valid from %(valid_from_hours)s:00 UTC on the %(valid_from_date)s to %(valid_till_hours)s:00 UTC on the %(valid_till_date)s")

        result = result % _header

        return(result)

    def _decode_group_header(self, header):
        result = ""
        _header = header

        from_str = "From %(from_hours)s:%(from_minutes)s on the %(from_date)s: "
        prob_str = "Probability %(probability)s%% of the following between %(from_hours)s:00 on the %(from_date)s and %(till_hours)s:00 on the %(till_date)s: "
        tempo_str = "Temporarily between %(from_hours)s:00 on the %(from_date)s and %(till_hours)s:00 on the %(till_date)s: "
        prob_tempo_str = "Probability %(probability)s%% of the following temporarily between %(from_hours)s:00 on the %(from_date)s and %(till_hours)s:00 on the %(till_date)s: "
        becmg_str = "Gradual change to the following between %(from_hours)s:00 on the %(from_date)s and %(till_hours)s:00 on the %(till_date)s: "

        if "type" in _header:
            # Add ordinal suffix
            if "from_date" in _header:
                from_suffix = self._get_ordinal_suffix(_header["from_date"])
                _header["from_date"] = _header["from_date"] + from_suffix
            if "till_date" in _header:
                till_suffix = self._get_ordinal_suffix(_header["till_date"])
                _header["till_date"] = _header["till_date"] + till_suffix

            if _header["type"] == "FM":
                result += from_str % { "from_date":    _header["from_date"], 
                                       "from_hours":   _header["from_hours"],
                                       "from_minutes": _header["from_minutes"] }
            elif _header["type"] == "PROB%s" % (_header["probability"]):
                result += prob_str % { "probability": _header["probability"],
                                       "from_date":   _header["from_date"], 
                                       "from_hours":  _header["from_hours"],
                                       "till_date":   _header["till_date"],
                                       "till_hours":  _header["till_hours"] }
            elif "PROB" in _header["type"] and "TEMPO" in _header["type"]:
                result += prob_tempo_str % { "probability": _header["probability"],
                                           "from_date":   _header["from_date"], 
                                           "from_hours":  _header["from_hours"],
                                           "till_date":   _header["till_date"],
                                           "till_hours":  _header["till_hours"] }
                                       
            elif _header["type"] == "TEMPO":
                result += tempo_str % { "from_date":  _header["from_date"], 
                                        "from_hours": _header["from_hours"], 
                                        "till_date":  _header["till_date"], 
                                        "till_hours": _header["till_hours"] }
            elif _header["type"] == "BECMG":
                result += becmg_str % { "from_date":  _header["from_date"], 
                                        "from_hours": _header["from_hours"], 
                                        "till_date":  _header["till_date"],
                                        "till_hours": _header["till_hours"] }

        return(result)

    def _decode_wind(self, wind):
        unit = ""
        result = ""

        if wind["direction"] == "000":
            return("calm")
        elif wind["direction"] == "VRB":
            result += "variable"
        else:
            result += "from %s degrees" % wind["direction"]

        if wind["unit"] == "KT":
            unit = "knots"
        elif wind["unit"] == "MPS":
            unit = "meters per second"
        else:
            # Unlikely, but who knows
            unit = "(unknown unit)"

        result += " at %s %s" % (wind["speed"], unit)

        if wind["gust"]:
            result += " gusting to %s %s" % (wind["gust"], unit)

        return(result)

    def _decode_visibility(self, visibility):
        result = ""

        if "more" in visibility:
            if visibility["more"]:
                result += "more than "

        result += visibility["range"]

        if visibility["unit"] == "SM":
            result += " statute miles"
        elif visibility["unit"] == "M":
            result += " meters"

        return(result)

    def _decode_clouds(self, clouds):
        result = ""
        i_result = ""
        list = []

        for layer in clouds:
            if layer["layer"] == "SKC" or layer["layer"] == "CLR":
                return "sky clear"

            if layer["layer"] == "NSC":
                return "no significant cloud"

            if layer["layer"] == "CAVOK":
                return "ceiling and visibility are OK"

            if layer["layer"] == "CAVU":
                return "ceiling and visibility unrestricted"

            if layer["layer"] == "SCT":
                layer_type = "scattered"
            elif layer["layer"] == "BKN":
                layer_type = "broken"
            elif layer["layer"] == "FEW":
                layer_type = "few"
            elif layer["layer"] == "OVC":
                layer_type = "overcast"

            if layer["type"] == "CB":
                type = "cumulonimbus"
            elif layer["type"] == "CU":
                type = "cumulus"
            elif layer["type"] == "TCU":
                type = "towering cumulus"
            elif layer["type"] == "CI":
                type = "cirrus"
            else:
                type = ""

            result = "%s %s clouds at %d feet" % (layer_type, type, int(layer["ceiling"])*100)

            # Remove extra whitespace, if any
            result = re.sub(r'\s+', ' ', result)

            list.append(result)

            layer = ""
            type = ""
            result = ""

        result = ", ".join(list)
        return(result)

    def _decode_weather(self, weather):
        result = ""
        i_result = ""
        ii_result = ""
        list = []

        for group in weather:
            # Special cases
            if "+" in group and "FC" in group:
                i_result += "tornado or watersprout"
                list.append(i_result)
                continue

            if "MI" in group:
                ii_result += "shallow "
            elif "BC" in group:
                ii_result += "patchy "
            elif "DR" in group:
                ii_result += "low drifting "
            elif "BL" in group:
                ii_result += "blowing "
            elif "SH" in group:
                ii_result += "showers "
            elif "TS" in group:
                ii_result += "thunderstorms "
            elif "FZ" in group:
                ii_result += "freezing "
            elif "PR" in group:
                ii_result = "partial "

            if "DZ" in group:
                ii_result += "drizzle"
            if "RA" in group:
                ii_result += "rain"
            if "SN" in group:
                ii_result += "snow"
            if "SG" in group:
                ii_result += "snow grains"
            if "IC" in group:
                ii_result += "ice"
            if "PL" in group:
                ii_result += "ice pellets"
            if "GR" in group:
                ii_result += "hail"
            if "GS" in group:
                ii_result += "small snow/hail pellets"
            if "UP" in group:
                ii_result += "unknown precipitation"
            if "BR" in group:
                ii_result += "mist"
            if "FG" in group:
                ii_result += "fog"
            if "FU" in group:
                ii_result += "smoke"
            if "DU" in group:
                ii_result += "dust"
            if "SA" in group:
                ii_result += "sand"
            if "HZ" in group:
                ii_result += "haze"
            if "PY" in group:
                ii_result += "spray"
            if "VA" in group:
                ii_result += "volcanic ash"
            if "PO" in group:
                ii_result += "dust/sand whirl"
            if "SQ" in group:
                ii_result += "squall"
            if "FC" in group:
                ii_result += "funnel cloud"
            if "SS" in group:
                ii_result += "sand storm"
            if "DS" in group:
                ii_result += "dust storm"

            # Fix the most ugly grammar
            if "SH" in group and "RA" in group:
                ii_result = "showers"
            if "SH" in group and "SN" in group:
                ii_result = "snow showers"
            if "SH" in group and "SG" in group:
                ii_result = "snow grain showers"
            if "SH" in group and "PL" in group:
                ii_result = "ice pellet showers"
            if "SH" in group and "IC" in group:
                ii_result = "ice showers"
            if "SH" in group and "GS" in group:
                ii_result = "snow pellet showers"
            if "SH" in group and "GR" in group:
                ii_result = "hail showers"

            if "TS" in group and "RA" in group:
                ii_result = "thunderstorms and rain"
            if "TS" in group and "UP" in group:
                ii_result = "thunderstorms with unknown precipitation"

            if "+" in group:
                i_result = "heavy %s" % ii_result
            elif "-" in group:
                i_result = "light %s" % ii_result
            elif "VC" in group:
                i_result = "%s in the vicinity" % ii_result
            else:
                i_result = ii_result

            list.append(i_result)
            i_result = ""
            ii_result = ""

        result = ", ".join(list)

        # Remove extra whitespace, if any
        result = re.sub(r'\s+', ' ', result)
        return(result)

    def _decode_windshear(self, windshear):
        result = "at %s, wind %s at %s %s" % ((int(windshear["altitude"])*100), windshear["direction"], windshear["speed"], windshear["unit"])
        return(result)

    def _decode_maintenance(self, maintenance):
        if maintenance:
            return "Station is under maintenance check\n"

    def _get_ordinal_suffix(self, date):
        _date = str(date)

        suffix = ""

        if re.match(".*(1[12]|[04-9])$", _date):
            suffix = "th"
        elif re.match(".*1$", _date):
            suffix = "st"
        elif re.match(".*2$", _date):
            suffix = "nd"
        elif re.match(".*3$", _date):
            suffix = "rd"

        return(suffix)

## translation of the present-weather codes into english
WEATHER_INT = {
    "-": "light",
    "+": "heavy",
    "-VC": "nearby light",
    "+VC": "nearby heavy",
    "VC": "nearby"
}
        

class TafGroup:

    ATTRIBUTES = ['wind', 'visibility', 'clouds', 'weather', 'windshear']
    
    def __init__(self, group, default_header, decoder):
        if not isinstance(group, dict):
            raise DecodeError("Argument is not a TAF parser object")

        self._group = group

        self.header = group['header']
        if not self.header:
            self.header = default_header
        self.type = self.header["type"]
        
        self.start_time = decoder._decode_timestamp(self.header, 'from_', 'valid_from_', 'origin_')
        self.end_time = decoder._decode_timestamp(self.header, 'till_')

        for attr in self.ATTRIBUTES:
            self._decode_attribute(attr)
        self._set_forecast()

    @staticmethod
    def get_attributes():
        return ['wind', 'visibility', 'clouds', 'weather', 'windshear']

    def header_starts_with(self, keys):
        for key in keys:
            if self.header["type"].startswith(key):
                return True
        return False

    def fill_in_information(self, other_group):
        for attr in self.ATTRIBUTES:
            value = getattr(self, attr, None)
            if not value or value.get(attr) == 0:
                setattr(self, attr, getattr(other_group, attr)) # override attr
            elif self.header['type'].startswith('PROB'):
                current_values = getattr(self, attr)
                for key,value in getattr(other_group, attr).items(): # override higher-probability values
                    if key not in current_values or self.forecast.get('prob', 100) < 50:
                        current_values[key] = value

        self._set_forecast()

    def _set_forecast(self):
        self.forecast = {}
        prob = self._get_prob()
        if prob:
            self.forecast['prob'] = int(prob)
        for attr in self.ATTRIBUTES:
            self.forecast.update(getattr(self, attr, {}))

    def _get_prob(self):
        return self.header.get('probability', None)

    def _decode_attribute(self, attr):
        methodToCall = getattr(self, '_decode_' + attr)
        methodToCall()

    def _decode_range(self, range_str):
        if ' ' in range_str:
            a, rem = range_str.split(' ')
            a = int(a)
        else:
            a = 0
            rem = range_str

        if '/' in rem:
            num, denom = rem.split('/')
            b = float(num) / int(denom)
        else:
            b = int(rem)

        result = a + b
        return result

    def _decode_visibility(self):
        vis = self._group.get('visibility', None)
        if not vis:
            self.visibility = {}
        else:
            range = self._decode_range(vis['range'])
            self.visibility = {'visibility_' + vis['unit']: range}
        
    def _decode_wind(self):
        wind = self._group.get('wind', None)
        if not wind or wind['direction'] == "000":
            self.wind = {'wind': 0}
            return

        data = {'wind': 1}

        wind_speed = int(wind["speed"])
        data['wind_speed_' + wind['unit']] = wind_speed

        if wind["direction"] == "VRB":
            data['wind_dir'] = -1
        else:
            wind_dir = int(wind["direction"])
            data['wind_dir'] = wind_dir
            wind_rad = math.radians(int(wind_dir))
            data['wind_crosswind_cos'] = round(wind_speed * math.cos(wind_rad), 2)
            data['wind_crosswind_sin'] = round(wind_speed * math.sin(wind_rad), 2)

        if wind['gust']:
            data['wind_gust_' + wind['unit']] = int(wind['gust'])

        self.wind = data

    def _decode_clouds(self):
        clouds = self._group.get('clouds', None)
        if not clouds:
            self.clouds = {}
            return

        if clouds[0]['layer'] in ["SKC", "CLR", "NSC", "CAVOK", "CAVU"]:
            self.clouds = {'sky_clear': 1, 'clouds_num_layers': 0}
            return

        data = {'clouds_num_layers': len(clouds)}
        for layer in clouds:
            for key,value in layer.items():
                if not value:
                    continue
                if key in ['layer', 'type']:
                    data['clouds_%s_%s' % (key, value)] = 1
                elif key == 'ceiling':
                    if 'clouds_ceiling_ft' not in data:
                        data['clouds_ceiling_ft'] = int(value)
                    else:
                        data['clouds_ceiling_max_ft'] = int(value)
            
        self.clouds = data

    def _decode_weather(self):
        weather = self._group.get('weather')
        if not weather:
            self.weather = {'weather': 0}
            return

        data = {'weather': 1}
        for wx in weather:
            for key, value in wx.items():
                if value == 'weather':
                    continue # Skipping the full weather string because it's represented in intensity, weather, and phenom
                elif value == 'intensity':
                    key = WEATHER_INT.get(key, None)
                if key:
                    data['wx_%s_%s' % (value, key)] = 1

        self.weather = data

    def _decode_windshear(self):
        windshear = self._group.get('windshear', None)
        if not windshear:
            self.windshear = {'windshear': 0}
            return

        self.windshear = {
            'windshear': 1,
            'windshear_alt_ft': windshear["altitude"],
            'windshear_dir': int(windshear["direction"]),
            'windshear_speed_' + windshear['unit']: int(windshear["speed"])
        }

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        rep = ''
        if self.start_time:
            rep += self.start_time.strftime('%d %H:%M-')
        else:
            rep += 'None-'
        if self.end_time:
            rep += self.end_time.strftime('%H:%M ')
        else:
            rep += 'None '
        rep += self.type + ' '
        #rep += str(self.forecast)
        return rep

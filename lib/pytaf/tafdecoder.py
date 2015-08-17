from calendar import monthrange
import re
from datetime import datetime, timedelta
import logging
from .taf import TAF

class DecodeError(Exception):
    def __init__(self, msg):
        self.strerror = msg

class Decoder(object):
    def __init__(self, taf, month=None, year=None):
        if isinstance(taf, TAF):
            self._taf = taf
            self._decode_groups(month=month, year=year)
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
            if group.start_time <= timestamp and timestamp < group.end_time:
#                print timestamp, group
                return group
        logging.warning('No group found for timestamp' + timestamp.isoformat())
        return None

    def _extract_time(self, header, *prefixes):
        if not header:
            raise ValueError('Expecting non-empty header')
        
        for prefix in prefixes:
            if header.get(prefix + 'date', None):
                day = int(header.get(prefix + 'date'))
                hour = int(header.get(prefix + 'hours'))
                minute = int(header.get(prefix + 'minutes', 0))
                return day, hour, minute
        raise ValueError('No time corresponding to prefixes: %s found in header: %s' % (prefixes, header))
        
    def _decode_timestamp(self, header, *prefixes):
        day, hours, minutes = self._extract_time(header, *prefixes)
        if hours == 24:
            hours = 23
            minutes = 59            
        
        month = self.issued_timestamp.month
        if self.issued_timestamp.day > day:
            month = ((self.issued_timestamp.month + 1) % 12) + 1
        month, day = self._normalize_date(self.issued_timestamp.year, month, day)
                
        return self.issued_timestamp.replace(month = month, day = day, hour = hours, minute = minutes)

    def _normalize_date(self, year, month, day):
        if day == 31:
            # Check if this month does not have 31 days, and change to valid date. This error occurs in the data.
            days_in_month = monthrange(year, month)[1]
            if days_in_month == 30:
                day = 1
                month += 1
        return month, day
        
    def _decode_groups(self, month, year):
        if month is None:
            month = 1
        if year is None:
            year = datetime.utcnow().year
            
        taf_header = self._taf.get_header()
        day, hours, minutes = self._extract_time(taf_header, 'origin_')
        month, day = self._normalize_date(year, month, day)
        self.issued_timestamp = datetime(year, month, day, hours, minutes)
        
        self.groups = [TafGroup(group, taf_header, self) for group in self._taf.get_groups()]
        for i, group in enumerate(self.groups[:-1]):
            nextgroup = self.groups[i+1]
            group.end_time = nextgroup.start_time
            # TODO: the dates may be off, because may need to iterate the month

        valid_till = self._decode_timestamp(taf_header, 'valid_till_')
        self.groups[-1].end_time = valid_till # set end time of last group

        self._complete_group_info()

    def _complete_group_info(self):
        # When PROB40, TEMPO, and BECMG are listed in the group header, this means that
        # components from the previous group are not expected to change in the group. Identify these cases,
        # and make sure each group contains complete information
        temp_keywords = ['PROB', 'TEMPO', 'BECMG']
        for index, group in enumerate(self.groups[1:]):
            if not group.header_starts_with(temp_keywords):
                continue

            # incorporate missing info from previous group
            prev_group = self.groups[index - 1]
            group.fill_in_information(prev_group)
        
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
            if group["intensity"] == "+" and group["phenomenon"] == "FC":
                i_result += "tornado or watersprout"
                list.append(i_result)
                continue

            if group["modifier"] == "MI":
                ii_result += "shallow "
            elif group["modifier"] == "BC":
                ii_result += "patchy "
            elif group["modifier"] == "DR":
                ii_result += "low drifting "
            elif group["modifier"] == "BL":
                ii_result += "blowing "
            elif group["modifier"] == "SH":
                ii_result += "showers "
            elif group["modifier"] == "TS":
                ii_result += "thunderstorms "
            elif group["modifier"] == "FZ":
                ii_result += "freezing "
            elif group["modifier"] == "PR":
                ii_result = "partial "

            if group["phenomenon"] == "DZ":
                ii_result += "drizzle"
            if group["phenomenon"] == "RA":
                ii_result += "rain"
            if group["phenomenon"] == "SN":
                ii_result += "snow"
            if group["phenomenon"] == "SG":
                ii_result += "snow grains"
            if group["phenomenon"] == "IC":
                ii_result += "ice"
            if group["phenomenon"] == "PL":
                ii_result += "ice pellets"
            if group["phenomenon"] == "GR":
                ii_result += "hail"
            if group["phenomenon"] == "GS":
                ii_result += "small snow/hail pellets"
            if group["phenomenon"] == "UP":
                ii_result += "unknown precipitation"
            if group["phenomenon"] == "BR":
                ii_result += "mist"
            if group["phenomenon"] == "FG":
                ii_result += "fog"
            if group["phenomenon"] == "FU":
                ii_result += "smoke"
            if group["phenomenon"] == "DU":
                ii_result += "dust"
            if group["phenomenon"] == "SA":
                ii_result += "sand"
            if group["phenomenon"] == "HZ":
                ii_result += "haze"
            if group["phenomenon"] == "PY":
                ii_result += "spray"
            if group["phenomenon"] == "VA":
                ii_result += "volcanic ash"
            if group["phenomenon"] == "PO":
                ii_result += "dust/sand whirl"
            if group["phenomenon"] == "SQ":
                ii_result += "squall"
            if group["phenomenon"] == "FC":
                ii_result += "funnel cloud"
            if group["phenomenon"] == "SS":
                ii_result += "sand storm"
            if group["phenomenon"] == "DS":
                ii_result += "dust storm"

            # Fix the most ugly grammar
            if group["modifier"] == "SH" and group["phenomenon"] == "RA":
                ii_result = "showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "SN":
                ii_result = "snow showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "SG":
                ii_result = "snow grain showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "PL":
                ii_result = "ice pellet showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "IC":
                ii_result = "ice showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "GS":
                ii_result = "snow pellet showers"
            if group["modifier"] == "SH" and group["phenomenon"] == "GR":
                ii_result = "hail showers"

            if group["modifier"] == "TS" and group["phenomenon"] == "RA":
                ii_result = "thunderstorms and rain"
            if group["modifier"] == "TS" and group["phenomenon"] == "UP":
                ii_result = "thunderstorms with unknown precipitation"

            if group["intensity"] == "+":
                i_result = "heavy %s" % ii_result
            elif group["intensity"] == "-":
                i_result = "light %s" % ii_result
            elif group["intensity"] == "VC":
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
        self.start_time = decoder._decode_timestamp(self.header, 'from_', 'valid_from_')
        self.end_time = None

        for attr in self.ATTRIBUTES:
            self._decode_attribute(attr)         
        self._set_forecast()

    def get_attributes(self):
        return ['wind', 'visibility', 'clouds', 'weather', 'windshear']

    def header_starts_with(self, keys):
        for key in keys:
            if self.header["type"].startswith(key):
                return True
        return False

    def fill_in_information(self, other_group):
        for attr in self.ATTRIBUTES:
            value = getattr(self, attr)
            if not value:
                setattr(self, attr, getattr(other_group, attr))

    def _set_forecast(self):
        self.forecast = {}
        for attr in self.ATTRIBUTES:
            self.forecast.update(getattr(self, attr))

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
            self.visibility = {'visibility' + vis['unit']: range}
        
    def _decode_wind(self):
        wind = self._group.get('wind', None)
        data = {'wind_none': 'TRUE'}
        if not wind or wind['direction'] == "000":
            self.wind = data
            return
        
        data['wind_none'] = "FALSE"
        if wind["direction"] == "VRB":
            data['wind_dir'] = -1
        else:
            data['wind_dir'] = wind["direction"]
        data['wind_speed_' + wind['unit']] = wind["speed"]
        if wind['gust']:
            data['wind_gust_' + wind['unit']] = wind['gust']

        self.wind = data

    def _decode_clouds(self):
        clouds = self._group.get('clouds', None)
        data = {}
        if clouds:
            data['clouds_num_layers'] = len(clouds)
            layer = clouds[0]
            data['clouds'] = layer["layer"]
            if not layer["layer"] in ["SKC", "CLR", "NSC", "CAVOK", "CAVU"]:
                data['clouds_type'] = layer["type"]
                data['clouds_ceiling_ft'] = int(layer["ceiling"])*100
            
        self.clouds = data

    def _decode_weather(self):
        weather = self._group.get('weather', None)
        data = {}
        if weather:
            wx = weather[0]
            for key in ['modifier', 'phenomenon']:
                data['wx_' + key] = wx.get(key, None)
            if 'intensity' in wx:
                data['wx_intensity'] = WEATHER_INT.get(wx.get(key))
        self.weather = data

    def _decode_windshear(self):
        windshear = self._group.get('windshear', None)
        data = {}
        if windshear:
            data['windshear_alt_ft'] = int(windshear["altitude"])*100
            data['windshear_dir'] = windshear["direction"]
            data['windshear_speed_' + windshear['unit']] = windshear["speed"]
        self.windshear = data

    def __str__(self):
        rep = ''
        if self.start_time:
            rep += self.start_time.strftime('%d %H:%M-') + self.end_time.strftime('%H:%M ')
        else:
            rep += 'None '
        rep += str(self.forecast)
        return rep

import unittest
import pytaf
from datetime import datetime


def _set_wx(name, contents, use_name=True):
    result = contents
    if not contents:
        result = {}
    if use_name:
        result.update({name: int(bool(contents))})
    return result


def set_weather(contents=None):
    return _set_wx('weather', contents)


def set_clouds(contents=None):
    if not contents:
        contents = {'sky_clear': 1, 'clouds_num_layers': 0}
    return contents


class TafTests(unittest.TestCase):

    def setUp(self):
        pass

    def parse_taf(self):
        t = pytaf.TAF(self.raw_taf)
        self.taf = pytaf.Decoder(t, self.timestamp)

    def assertWeatherEquals(self, expected_weather, expected_clouds):
        self.assertEqual(self.group.weather, expected_weather)
        self.assertEqual(self.group.clouds, expected_clouds)

    def test_combound_weather(self):
        self.raw_taf = """
        TAF KMSP 212348Z 2200/2306 11010KT P6SM BKN250 FM220600 11011KT
          P6SM SCT080 BKN110 FM221100 11012KT 5SM -SNPL SCT035
          BKN050
         TEMPO 2211/2215 2SM -PL BKN030 OVC050 FM221600 11014KT
          6SM -RAPL SCT020 OVC035 FM222300 11014G20KT 1 1/2SM -SN
          OVC009=
        """
        self.timestamp = datetime(2016, 11, 21, 23, 48)
        self.parse_taf()

        group = self.taf.get_group(datetime(2016, 11, 22, 20, 00))
        self.assertEqual(group.weather, set_weather({
                                         'wx_phenomenon_PL': 1,
                                         'wx_phenomenon_RA': 1,
                                         'wx_intensity_light': 1}))

    def test_complex_weather(self):
        self.raw_taf = """
        TAF KIAH 230259Z 2303/2406 16010KT P6SM VCSH FEW028 SCT050 BKN250 FM230900
         18007KT P6SM -RA VCTS SCT015 BKN035CB
        TEMPO 2311/2314 TSRA FM231600 32010KT P6SM SCT250 FM240000
         34004KT P6SM SKC="""
        self.timestamp = datetime(2016, 11, 23, 2, 59)
        self.parse_taf()

        self.group = self.taf.get_group(datetime(2016, 11, 23, 3, 00))
        expected_group1_weather = set_weather({'wx_modifier_SH': 1, 'wx_intensity_nearby': 1})
        expected_group1_clouds = set_clouds({'clouds_ceiling_max_ft': 250, 'clouds_num_layers': 3,
                                             'clouds_layer_SCT': 1, 'clouds_ceiling_ft': 28, 'clouds_layer_FEW': 1,
                                             'clouds_layer_BKN': 1})
        self.assertWeatherEquals(expected_group1_weather, expected_group1_clouds)

        self.group = self.taf.get_group(datetime(2016, 11, 23, 10, 00))
        expected_group2_weather = set_weather({'wx_phenomenon_RA': 1, 'wx_modifier_TS': 1, 'wx_intensity_light': 1, 'wx_intensity_nearby': 1})
        expected_group2_clouds = set_clouds({'clouds_ceiling_ft': 15, 'clouds_layer_BKN': 1, 'clouds_ceiling_max_ft': 35, 'clouds_layer_SCT': 1, 'clouds_type_CB': 1, 'clouds_num_layers': 2})
        self.assertWeatherEquals(expected_group2_weather, expected_group2_clouds)

        self.group = self.taf.get_group(datetime(2016, 11, 23, 11, 00))
        expected_group3_weather = set_weather({'wx_modifier_TS': 1, 'wx_phenomenon_RA': 1})
        self.assertWeatherEquals(expected_group3_weather, expected_group2_clouds) # clouds stay the same as previous period

        self.group = self.taf.get_group(datetime(2016, 11, 23, 15, 00))
        self.assertWeatherEquals(expected_group2_weather, expected_group2_clouds)

        self.group = self.taf.get_group(datetime(2016, 11, 23, 16, 00))
        expected_group4_clouds = set_clouds({'clouds_ceiling_ft': 250, 'clouds_layer_SCT': 1, 'clouds_num_layers': 1})
        self.assertWeatherEquals(set_weather(), expected_group4_clouds)

        self.group = self.taf.get_group(datetime(2016, 11, 24, 5, 55))
        self.assertWeatherEquals(set_weather(), set_clouds())

    def test_wind_gusts(self):
        self.raw_taf = """
        TAF KEWR 230232Z 2303/2406 30012G18KT P6SM BKN040 FM230400 30011G17KT
          P6SM SCT040 FM230600 29009KT P6SM SCT040 FM231400 31011G17KT
          P6SM SKC FM240200 34005KT P6SM BKN250=
        :return:
        """

        self.timestamp = datetime(2016, 11, 23, 2, 32)
        self.parse_taf()

        self.group = self.taf.get_group(datetime(2016, 11, 23, 14, 35))
        self.assertEquals(self.group.forecast, {
            'wind': 1, 'wind_dir': 310, 'windshear': 0,
            'wind_speed_KT': 11, 'wind_gust_KT': 17,
            'wind_crosswind_cos': 7.07, 'wind_crosswind_sin': -8.43, 'wind_gust_diff_KT': 6,
            'clouds_num_layers': 0, 'sky_clear': 1,
            'visibility_SM': 6, 'weather': 0,
            })

    def test_tempo_section(self):
        self.raw_taf = """
        TAF KIAH 230259Z 2303/2406 16010KT P6SM VCSH FEW028 SCT050 BKN250 FM230900
          18007KT P6SM -RA VCTS SCT015 BKN035CB
         TEMPO 2311/2314 TSRA FM231600 32010KT P6SM SCT250 FM240000
          34004KT P6SM SKC=
        :return:
        """
        self.timestamp = datetime(2016, 11, 23, 2, 59)
        self.parse_taf()

        self.group = self.taf.get_group(datetime(2016, 11, 23, 12, 0))
        self.assertEquals(self.group.forecast, {
            'clouds_type_CB': 1, 'clouds_num_layers': 2, 'clouds_layer_BKN': 1, 'clouds_ceiling_max_ft': 35,
            'clouds_layer_SCT': 1, 'clouds_ceiling_ft': 15,
            'wind': 1, 'wind_crosswind_cos': -7.0, 'wind_dir': 180, 'wind_crosswind_sin': 0.0, 'wind_speed_KT': 7,
            'windshear': 0,
            'visibility_SM': 6,
            'weather': 1, 'wx_phenomenon_RA': 1, 'wx_modifier_TS': 1
        })

    def test_prob_forecast(self):
        self.raw_taf = """
        TAF KMSP 212111Z 2121/2224 11011KT P6SM BKN250 FM220400 11011KT P6SM
          SCT080 BKN110 FM221000 11012KT P6SM -SN SCT035 BKN050
          FM221200 11014KT 3SM -SN SCT020 OVC035
         PROB30 2212/2215 4SM -SNPL OVC020 FM221500 11014G20KT
          2SM -SNPL SCT009 OVC020 FM221800 11014G20KT 2SM -SNRA
          OVC009="""
        self.timestamp = datetime(2016, 11, 21, 11, 11)
        self.parse_taf()

        self.group = self.taf.get_group(datetime(2016, 11, 22, 13, 10))
        self.assertEquals(self.group.forecast, {
            'prob': 30,
            'clouds_ceiling_ft': 20, 'clouds_layer_SCT': 1, 'clouds_ceiling_max_ft': 35,
            'clouds_num_layers': 2, 'clouds_layer_OVC': 1,
            'wind': 1, 'wind_dir': 110, 'wind_speed_KT': 14, 'wind_crosswind_sin': 13.16, 'wind_crosswind_cos': -4.79,
            'weather': 1, 'wx_intensity_light': 1, 'wx_phenomenon_SN': 1, 'wx_phenomenon_PL': 1,
            'windshear': 0,
            'visibility_SM': 3,
        })

    def test_vertical_visibility(self):
        self.raw_taf = """
        TAF KMSP 272329Z 2800/2906 12008KT 2SM -SNPL BR OVC007 FM280500
                       VRB03KT 1 1/2SM -FZDZ BR OVC006 FM280900
                       VRB03KT 1 1/2SM BR OVC004
                      TEMPO 2810/2814 1/2SM FZFG VV002 FM281700
                       17005KT 5SM BR SCT004 OVC008 FM290400 13004KT
                       3SM -FZRA BR OVC005
        """

        self.timestamp = datetime(2013, 1, 27, 23, 29)
        self.parse_taf()

        self.group = self.taf.get_group(datetime(2013, 1, 28, 10, 30))
        self.assertEquals(self.group.forecast, {
            'wind': 1, 'wind_dir_variable': 1, 'wind_speed_KT': 3, 'windshear': 0,
            'weather': 1, 'wx_modifier_FZ': 1, 'wx_phenomenon_FG': 1,
            'visibility_vertical_ft': 2, 'visibility_SM': 0.5,
            'clouds_layer_OVC': 1, 'clouds_ceiling_ft': 4, 'clouds_num_layers': 1,
        })
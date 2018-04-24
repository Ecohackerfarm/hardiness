import os
import pygeohash
import pprint
import datetime
from influxdb import InfluxDBClient
from statistics import mean
import folium
import json
import shapely.errors
from shapely.geometry import shape
import pandas as pd
from geojson import FeatureCollection, Feature, Polygon
from scipy.spatial import Voronoi

client = InfluxDBClient(host='127.0.0.1', port=8086, database='noaa')
pp = pprint.PrettyPrinter(indent=4)
DIR = "/home/leaf/Downloads/noaa/"
mapVor = folium.Map(location=[40.75, -73.9], zoom_start=2)

def send_dict_to_influx(data):
    for station in data:
        metrics = dict()
        metrics['measurement'] = "temperature"
        metrics['tags'] = {}
        metrics['fields'] = {}
        metrics['tags']['station_name'] = data[station]["STATION NAME"]
        if not data[station]["GEOHASH"]:
            continue
        metrics['tags']['geohash'] = data[station]["GEOHASH"]
        metrics['tags']['country'] = data[station]["CTRY"]
        metrics['tags']['usaf_code'] = data[station]['USAF']
        if data[station]["ST"]:
            metrics['tags']['state'] = data[station]["ST"]
        for entry in data[station]["DAILY_TEMPS"]:
            timestamp, temp = entry
            if timestamp[:4] == "2017":
                metrics['time'] = datetime.datetime.strptime(timestamp, "%Y%m%d%H%M").isoformat()
                metrics['fields']['current_temp'] = temp
                client.write_points([metrics])
            else:
                continue
        for year in data[station]["YEARLY_TEMPS"].keys():
            metrics['time'] = year + "-12-31T00:00:00Z00:00"
            metrics['fields']["min_avg_yearly"] = float(data[station]["YEARLY_TEMPS"][year])
        client.write_points([metrics])

def colorgrad(minimum, maximum, value):
    minimum, maximum = float(minimum), float(maximum)
    ratio = 2 * (value-minimum) / (maximum - minimum)
    b = int(max(0, 255*(1 - ratio)))
    g = int(max(0, 255*(ratio - 1)))
    r = 255 - b - g
    hexcolor = '#%02x%02x%02x' % (r,g,b)
    return hexcolor

def get_station_info(usaf_num):
    station_dict = {}
    station_info = []
    with open(DIR+"supportfiles/isd-history.txt") as support_file:
        for line in support_file:
            if line.startswith("USAF"):
                col_headers = divide_station_line(line)
            if line.startswith(usaf_num):
                station_info = divide_station_line(line)
        info_i = 0
        for column in col_headers:
            station_dict[column] = station_info[info_i]
            info_i += 1
    return station_dict


def divide_station_line(line):
    line_entry = []
    line_entry.append(line[0:7])
    line_entry.append(line[7:13])
    line_entry.append(line[13:43])
    line_entry.append(line[43:48])
    line_entry.append(line[48:51])
    line_entry.append(line[51:57])
    line_entry.append(line[57:65])
    line_entry.append(line[65:74])
    line_entry.append(line[74:82])
    line_entry.append(line[82:91])
    line_entry.append(line[91:94])
    for i in range(0, len(line_entry)-1):
        line_entry[i] = line_entry[i].strip()
    return line_entry


def parse_data_date(line):
    ymd = line[13:21]
    hm = line[21:25]
    return ymd, hm


def parse_data_temp(line):
    unadj_temp = int(line[83:88])
    temp = unadj_temp
    alt = line[100:106]
    if not '*' in alt:
        alt = float(alt)
        if alt > 400:
            temp = unadj_temp - alt/float(1000)*3.5
    return temp


info_list = []
coorddict = dict()
coords = list()
infodict = dict()
directories = [x[1] for x in os.walk(DIR)][0]
hcol = None

for directory in directories:
    for filename in os.listdir(DIR + directory + "/"):

        if os.path.isdir(DIR + directory + filename) or not filename.endswith("out"):
            continue

        with open(DIR + directory + "/" + filename) as file:
            data = file.readlines()[1:]
            first = True
            prev_date = ""
            all_temps = []
            year_record = []

            for line in data:
                date, hm = parse_data_date(line)
                if hm == "2400":
                    hm = "0000"
                timestamp = date + hm
                try:
                    year_record.append((timestamp, parse_data_temp(line)))
                except ValueError:
                    continue

                if first:
                    prev_date = date
                    first = False
                    day_temps = []

                if date == prev_date:
                    temp = parse_data_temp(line)
                    day_temps.append(temp)
                else:
                    # keep the three lowest temps
                    if not any(day_temps):
                        continue
                    for i in range(0, len(day_temps)-1):
                        if i >= len(day_temps)-1:
                            break
                        if day_temps[i] > 160 or day_temps[i] < -110:
                            day_temps.pop(i)
                    day_temps.sort()
                    coldest_day_temps = day_temps[0:3]
                    day_temps = []
                    if any(coldest_day_temps):
                        all_temps.append(mean(coldest_day_temps))
                    # add the mean day temperature to the list of all day temps
                    first = True

            # sort all the year's daily temperatures
            all_temps.sort()
            # if there's not any reported temperatures, we can skip this file
            if not any(all_temps):
                break
            # get a week's worth of coldest temps
            min_avg_temp = mean(all_temps[0:7])
            station_info = filename.strip('.out').split('-')
            station_info.append(min_avg_temp)
            infodict[station_info[0]] = {"YEARLY_TEMPS": {station_info[2]: station_info[3]}}
            infodict[station_info[0]]["DAILY_TEMPS"] = year_record

tempdict = {}
for key in infodict.keys():
    tempdict = get_station_info(key)
    lat = None
    lon = None
    if tempdict["LAT"]:
        tempdict["LAT"] = float(tempdict["LAT"])
        lat = tempdict["LAT"]
    if tempdict["LON"]:
        tempdict["LON"] = float(tempdict["LON"])
        lon = tempdict["LON"]
    if lat and lon:
        geohash = pygeohash.encode(lat, lon)
        tempdict["GEOHASH"] = geohash
        coorddict[tempdict["STATION NAME"]] = {"lat": lat, "lon": lon, "temp": mean(infodict[key]["YEARLY_TEMPS"].values())}
    if tempdict["ELEV(M)"]:
        elevation = float(tempdict["ELEV(M)"][1:])
        tempdict["ELEV(M)"] = elevation
        for year in infodict[key]["YEARLY_TEMPS"].keys():
            infodict[key]["YEARLY_TEMPS"] = infodict[key]["YEARLY_TEMPS"][year] - (elevation/float(1000) * 3.5)

    else:
        tempdict["GEOHASH"] = False
    for k, v in infodict[key].items():
        tempdict[k] = v
    infodict[key] = tempdict

station_csv = open('station_temps.csv', 'w')
print("id,lat,lon,temp".strip(), file=station_csv)
for station in coorddict.keys():
    print(",".join([str(station).strip(), str(coorddict[station]["lat"]), str(coorddict[station]["lon"]), str(coorddict[station]["temp"])]).strip(), file=station_csv)
    coords.append((coorddict[station]["lat"], coorddict[station]["lon"]))
station_csv.close()

vor = Voronoi(coords)
#voronoi_plot_2d(vor)
#The output file, to contain the Voronoi diagram we computed:
vorJSON = open('libVor.json', 'w')
point_voronoi_list = []
feature_list = []
i = 0
for region in range(len(vor.regions)-1):
    vertex_list = []
    for x in vor.regions[region]:
        #Not sure how to map the "infinite" point, so, leave off those regions for now:
        if x == -1:
            break
        else:
            #Get the vertex out of the list, and flip the order for folium:
            vertex = vor.vertices[x]
            vertex = (vertex[1], vertex[0])
        vertex_list.append(vertex)
    #Save the vertex list as a polygon and then add to the feature_list:
    polygon = Polygon([vertex_list])
    for entry in coorddict.keys():
#        print(i, vor.points[i][0], vor.points[i][1], str(coorddict[entry]["lat"]), str(coorddict[entry]["lon"]))
        if vor.points[i][0] == coorddict[entry]["lat"] and vor.points[i][1] == coorddict[entry]["lon"]:
            id = entry
            break
    else:
        print("no name found, aborting...")
        exit(1)
    feature = Feature(geometry=polygon, properties={"id": id})
    feature_list.append(feature)
    i += 1

# create a new feature list with just the intersections of our world map land boundaries and our created polygons
bordered_feature_list = list()
with open("world.json") as worldjson:
    countries = json.loads(worldjson.read())
    for country in countries["features"]:
        border = country["geometry"]
        polygon = shape(border)
        if polygon:
            for p in feature_list:
                try:
                    polycoords = p["geometry"]["coordinates"]
                    if len(polycoords[0]) < 3:
                        continue
                except:
                    continue
                polygon2 = shape(p["geometry"])
                try:
                    newpoly = polygon.buffer(0.0).intersection(polygon2.buffer(0.0))
                except shapely.errors.TopologicalError as error:
                    print(error)
                    continue
                feature = Feature(geometry=newpoly, properties={"id": p["properties"]["id"]})
                bordered_feature_list.append(feature)

#Write the features to the new file:
feature_collection = FeatureCollection(bordered_feature_list)
print(feature_collection, file=vorJSON)
vorJSON.close()


#Add the voronoi layer to the map:
#folium.GeoJson('libVor.json', name="geojson").add_to(mapVor)
pd_stations = pd.read_csv('station_temps.csv', sep='\s*,\s*', encoding="utf-8-sig", delimiter=',')
#print(pd_stations)
mapVor.choropleth(geo_data='libVor.json', data=pd_stations, columns=['id', 'temp'], key_on='properties.id', fill_color="YlGn", fill_opacity=0.45, line_opacity=0.1)
folium.LayerControl().add_to(mapVor)
mapVor.save(outfile='libVor.html')

#send_dict_to_influx(infodict)



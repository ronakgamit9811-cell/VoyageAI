import os
import csv
import json
import urllib.request
import urllib.parse
import sqlite3
from flask import Flask, render_template, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

DATABASE_PATH = "voyage.db"

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            bio TEXT,
            avatar TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            destination_name TEXT NOT NULL,
            trip_data_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_data_json TEXT NOT NULL,
            UNIQUE(user_email, item_id) ON CONFLICT REPLACE
        )
    """)
    conn.commit()
    conn.close()

# Initialize DB tables
init_db()

# Optional AI / Maps integration
client = None
gmaps = None
active_ai_model = None
GOOGLE_MAPS_API_KEY = ""

def get_openai_client():
    global client, active_ai_model
    load_dotenv(override=True)
    gemini_key = os.getenv("GEMINI_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    if gemini_key:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
            )
            active_ai_model = "gemini-2.5-flash"
        except Exception:
            client = None
    elif openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            active_ai_model = "gpt-3.5-turbo"
        except Exception:
            client = None
    else:
        client = None
        active_ai_model = None
        
    return client

def get_gmaps_client():
    global gmaps, GOOGLE_MAPS_API_KEY
    load_dotenv(override=True)
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
    
    if GOOGLE_MAPS_API_KEY:
        try:
            import googlemaps
            gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
        except Exception:
            gmaps = None
    else:
        gmaps = None
        
    return gmaps

app = Flask(__name__, static_folder="frontend/dist", static_url_path="")
CORS(app, origins=["http://localhost:5173", "http://127.0.0.1:5173"])

# =========================
# LOAD DATA
# =========================
places = []
cities = set()
city_images = {}

with open("tourist.csv", "r", encoding="utf-8") as file:
    reader = csv.DictReader(file)
    for row in reader:
        places.append(row)
        c_name = row.get("city", row["name"])
        cities.add(c_name)
        if row.get("type", "").lower() == "city":
            if row.get("image"):
                city_images[c_name.lower()] = row["image"]

# Helper for google maps image
def get_gmaps_image(query):
    gmaps_client = get_gmaps_client()
    if not gmaps_client:
        return ""
    try:
        places_result = gmaps_client.places(query=query)
        if places_result.get('results'):
            place = places_result['results'][0]
            photos = place.get('photos')
            if photos:
                photo_ref = photos[0]['photo_reference']
                return f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=800&photoreference={photo_ref}&key={GOOGLE_MAPS_API_KEY}"
    except Exception:
        pass
    return ""

# Helper for OpenAI description
def get_openai_desc(place, city):
    ai_client = get_openai_client()
    if not ai_client:
        return ""
    try:
        prompt = f"Write a very short 2-sentence engaging travel description for {place} in {city}."
        res = ai_client.chat.completions.create(
            model=active_ai_model or "gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        return res.choices[0].message.content.strip()
    except Exception:
        pass
    return ""

# =========================
# HOME ROUTE
# =========================
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

# =========================
# DATABASE REST APIS
# =========================

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    if not email:
        return {"error": "Email is required"}, 400
    
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    if user is None:
        # Auto-register new explorer with default details
        prefix = email.split('@')[0]
        clean_name = ''.join(c if c.isalnum() else ' ' for c in prefix).strip()
        parts = [p for p in clean_name.split(' ') if p]
        first_name = parts[0].capitalize() if parts else "Traveler"
        last_name = parts[1].capitalize() if len(parts) > 1 else ""
        
        avatar = f"https://ui-avatars.com/api/?name={urllib.parse.quote(first_name + ' ' + last_name)}&background=7c3aed&color=fff&size=128"
        bio = "Avid explorer and photographer. Constantly looking for the next hidden gem across the globe. Powered by AI and coffee."
        
        conn.execute(
            "INSERT INTO users (email, first_name, last_name, bio, avatar) VALUES (?, ?, ?, ?, ?)",
            (email, first_name, last_name, bio, avatar)
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    
    user_dict = dict(user)
    conn.close()
    return user_dict

@app.route("/api/user/profile", methods=["GET", "POST"])
def manage_profile():
    if request.method == "POST":
        data = request.get_json() or {}
        email = data.get("email", "").strip().lower()
        if not email:
            return {"error": "Email is required to update profile"}, 400
            
        first_name = data.get("firstName")
        last_name = data.get("lastName")
        bio = data.get("bio")
        avatar = data.get("avatar")
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            conn.close()
            return {"error": "User profile not found"}, 404
            
        update_fields = []
        params = []
        if first_name is not None:
            update_fields.append("first_name = ?")
            params.append(first_name)
        if last_name is not None:
            update_fields.append("last_name = ?")
            params.append(last_name)
        if bio is not None:
            update_fields.append("bio = ?")
            params.append(bio)
        if avatar is not None:
            update_fields.append("avatar = ?")
            params.append(avatar)
            
        if update_fields:
            query = f"UPDATE users SET {', '.join(update_fields)} WHERE email = ?"
            params.append(email)
            conn.execute(query, params)
            conn.commit()
            
        updated_user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        user_dict = dict(updated_user)
        conn.close()
        return user_dict
    else: # GET
        email = request.args.get("email", "").strip().lower()
        if not email:
            return {"error": "Email is required"}, 400
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if not user:
            return {"error": "User not found"}, 404
        return dict(user)

@app.route("/api/trips", methods=["GET", "POST"])
def manage_trips():
    if request.method == "POST":
        data = request.get_json() or {}
        email = data.get("email", "").strip().lower()
        trip_data = data.get("trip")
        
        if not email or not trip_data:
            return {"error": "Email and trip data are required"}, 400
            
        destination = trip_data.get("destinationName", "Eco Trip")
        trip_json = json.dumps(trip_data)
        
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO saved_trips (user_email, destination_name, trip_data_json) VALUES (?, ?, ?)",
            (email, destination, trip_json)
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "Trip successfully saved"}
        
    else: # GET
        email = request.args.get("email", "").strip().lower()
        if not email:
            return {"error": "Email is required"}, 400
            
        conn = get_db_connection()
        rows = conn.execute("SELECT trip_data_json FROM saved_trips WHERE user_email = ? ORDER BY id DESC", (email,)).fetchall()
        trips = [json.loads(row["trip_data_json"]) for row in rows]
        conn.close()
        return {"trips": trips}

@app.route("/api/favorites", methods=["GET", "POST"])
def manage_favorites():
    if request.method == "POST":
        data = request.get_json() or {}
        email = data.get("email", "").strip().lower()
        item = data.get("item")
        
        if not email or not item:
            return {"error": "Email and favorite item are required"}, 400
            
        item_id = item.get("id")
        item_type = item.get("type")
        item_json = json.dumps(item)
        
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO favorites (user_email, item_id, item_type, item_data_json) VALUES (?, ?, ?, ?)",
            (email, item_id, item_type, item_json)
        )
        conn.commit()
        conn.close()
        return {"success": True, "message": "Item favorited"}
        
    else: # GET
        email = request.args.get("email", "").strip().lower()
        if not email:
            return {"error": "Email is required"}, 400
            
        conn = get_db_connection()
        rows = conn.execute("SELECT item_data_json FROM favorites WHERE user_email = ?", (email,)).fetchall()
        favorites = [json.loads(row["item_data_json"]) for row in rows]
        conn.close()
        return {"favorites": favorites}

@app.route("/api/favorites/delete", methods=["POST"])
def delete_favorite():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    item_id = data.get("itemId")
    
    if not email or not item_id:
        return {"error": "Email and itemId are required"}, 400
        
    conn = get_db_connection()
    conn.execute("DELETE FROM favorites WHERE user_email = ? AND item_id = ?", (email, item_id))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Favorite deleted"}

@app.route("/api/place-details")
def place_details():
    query = request.args.get("q", "").strip()
    if not query:
        return {"error": "No query provided"}, 400
        
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    
    resolved_lat = 0.0
    resolved_lng = 0.0
    formatted_address = query
    category = "Scenic Destination"
    
    rating = None
    country = None
    hotel_per_day = None
    flight_cost = None
    train_cost = None
    bus_cost = None

    clean_query = query.split(',')[0].strip()

    # Search in offline tourist.csv database records
    for p in places:
        p_name = p.get("name", "").strip()
        p_city = p.get("city", "").strip()
        if p_name.lower() == clean_query.lower() or p_city.lower() == clean_query.lower():
            category = p.get("type", category)
            rating = p.get("rating")
            country = p.get("country")
            hotel_per_day = p.get("hotel_per_day")
            flight_cost = p.get("flight_cost")
            train_cost = p.get("train_cost")
            bus_cost = p.get("bus_cost")
            break
    
    if lat and lng:
        try:
            resolved_lat = float(lat)
            resolved_lng = float(lng)
        except ValueError:
            pass
            
    if resolved_lat == 0.0 or resolved_lng == 0.0:
        gmaps_client = get_gmaps_client()
        if gmaps_client:
            try:
                res = gmaps_client.geocode(query)
                if res:
                    loc = res[0]['geometry']['location']
                    resolved_lat = loc['lat']
                    resolved_lng = loc['lng']
                    formatted_address = res[0].get('formatted_address', query)
            except Exception:
                pass
        if resolved_lat == 0.0 or resolved_lng == 0.0:
            try:
                headers = {"User-Agent": "VoyageAITripPlanner/1.0 (ronak@voyageai.com)"}
                url = f"https://nominatim.openstreetmap.org/search?format=json&q={urllib.parse.quote(query)}&limit=1"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    if data:
                        resolved_lat = float(data[0]["lat"])
                        resolved_lng = float(data[0]["lon"])
                        formatted_address = data[0].get("display_name", query)
                        cls = data[0].get("class", "")
                        typ = data[0].get("type", "")
                        if cls in ["historic", "tourism", "amenity", "natural", "leisure"]:
                            category = f"{cls.capitalize()} ({typ.replace('_', ' ').capitalize()})"
            except Exception:
                pass
                
    description = f"Welcome to {query}. A beautifully preserved location perfect for green travel and sightseeing."
    image_url = ""
    clean_query = query.split(',')[0].strip()
    
    try:
        wiki_title = None
        if resolved_lat != 0.0 and resolved_lng != 0.0:
            try:
                geo_url = f"https://en.wikipedia.org/w/api.php?action=query&list=geosearch&gsradius=15000&gscoord={resolved_lat}|{resolved_lng}&format=json&origin=*"
                req = urllib.request.Request(geo_url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    results = data.get("query", {}).get("geosearch", [])
                    if results:
                        for r in results:
                            if clean_query.lower() in r["title"].lower():
                                wiki_title = r["title"]
                                break
                        if not wiki_title:
                            wiki_title = results[0]["title"]
            except Exception:
                pass
                
        if not wiki_title:
            try:
                search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(clean_query)}&format=json&origin=*"
                req = urllib.request.Request(search_url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    results = data.get("query", {}).get("search", [])
                    if results:
                        wiki_title = results[0]["title"]
            except Exception:
                pass
                
        if wiki_title:
            try:
                sum_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(wiki_title)}"
                req = urllib.request.Request(sum_url, headers={"User-Agent": "VoyageAI/1.0"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    summary_data = json.loads(resp.read().decode("utf-8"))
                    if "extract" in summary_data:
                        description = summary_data["extract"]
                    if "thumbnail" in summary_data and "source" in summary_data["thumbnail"]:
                        image_url = summary_data["thumbnail"]["source"]
            except Exception:
                pass
    except Exception:
        pass
        
    if not image_url:
        nature_fallback_images = [
            "https://images.unsplash.com/photo-1501785888041-af3ef285b470?w=800&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=800&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1447752875215-b2761acb3c5d?w=800&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=800&auto=format&fit=crop",
            "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop"
        ]
        idx = sum(ord(c) for c in clean_query) % len(nature_fallback_images)
        image_url = nature_fallback_images[idx]
        
    return {
        "name": clean_query,
        "lat": resolved_lat,
        "lng": resolved_lng,
        "address": formatted_address,
        "category": category,
        "description": description,
        "imageUrl": image_url,
        "rating": rating,
        "country": country,
        "hotelCost": hotel_per_day,
        "flightCost": flight_cost,
        "trainCost": train_cost,
        "busCost": bus_cost
    }

# =========================
# GEOCODE ROUTE
# =========================
@app.route("/geocode")
def geocode():
    query = request.args.get("q", "")
    if not query:
        return {"lat": 0, "lng": 0, "error": "No query"}
        
    # 1. Try Google Maps Geocoding if key is present
    gmaps_client = get_gmaps_client()
    if gmaps_client:
        try:
            res = gmaps_client.geocode(query)
            if res:
                location = res[0]['geometry']['location']
                return {"lat": location['lat'], "lng": location['lng']}
        except Exception:
            pass
            
    # 2. Try OpenStreetMap Nominatim API as fallback
    try:
        headers = {"User-Agent": "VoyageAITripPlanner/1.0 (ronak@voyageai.com)"}
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={urllib.parse.quote(query)}&limit=1"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data:
                lat = float(data[0]["lat"])
                lng = float(data[0]["lon"])
                return {"lat": lat, "lng": lng}
    except Exception as e:
        print("Geocoding Fallback Error:", e)
        
    return {"lat": 0, "lng": 0, "error": "Not found"}

# =========================
# CONFIG ROUTE
# =========================
@app.route("/api/config")
def get_config():
    get_gmaps_client()
    return {
        "gmaps_key": GOOGLE_MAPS_API_KEY or ""
    }

# =========================
# AUTOCOMPLETE (CITY)
# =========================
@app.route("/suggest")
def suggest():
    query = request.args.get("q", "").lower()

    results = []
    
    # 1. Query Google Places Autocomplete if gmaps client is initialized
    gmaps_client = get_gmaps_client()
    if query and gmaps_client:
        try:
            autocomplete_res = gmaps_client.places_autocomplete(input_text=query)
            for prediction in autocomplete_res:
                description = prediction['description']
                img = city_images.get(description.split(',')[0].strip().lower(), "")
                results.append({"name": description, "image": img})
        except Exception as e:
            print("Google Autocomplete Error:", e)

    # 2. Merge and query local cities as fallback/supplement
    for city in cities:
        if query in city.lower():
            # Avoid duplicate entries
            if not any(r["name"].lower() == city.lower() or r["name"].lower().startswith(city.lower()) for r in results):
                img = city_images.get(city.lower(), "")
                results.append({"name": city, "image": img})

    # Sort results to have exact query matches first, then take top 5
    results.sort(key=lambda x: (not x["name"].lower().startswith(query), x["name"]))
    return {"suggestions": results[:5]}


# =========================
# GEODETIC & WEATHER UTILITIES
# =========================
geocode_cache = {}

def geocode_cached(query):
    if not query:
        return None
    clean_query = query.strip()
    if clean_query in geocode_cache:
        return geocode_cache[clean_query]
    
    # 1. Try Google Maps Geocoding
    gmaps_client = get_gmaps_client()
    if gmaps_client:
        try:
            res = gmaps_client.geocode(clean_query)
            if res:
                location = res[0]['geometry']['location']
                coords = {"lat": location['lat'], "lng": location['lng']}
                geocode_cache[clean_query] = coords
                return coords
        except Exception:
            pass
            
    # 2. Try Nominatim Fallback
    try:
        headers = {"User-Agent": "VoyageAITripPlanner/1.0 (ronak@voyageai.com)"}
        url = f"https://nominatim.openstreetmap.org/search?format=json&q={urllib.parse.quote(clean_query)}&limit=1"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data:
                coords = {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
                geocode_cache[clean_query] = coords
                return coords
    except Exception as e:
        print("Fallback Geocoding Error for:", clean_query, e)
        
    return None

def fetch_live_weather(lat, lng):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lng}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=3"
        req = urllib.request.Request(url, headers={"User-Agent": "VoyageAITripPlanner/1.0 (ronak@voyageai.com)"})
        with urllib.request.urlopen(req, timeout=5) as response:
            wdata = json.loads(response.read().decode("utf-8"))
            
            # Map Open-Meteo weather code to condition string
            code = wdata.get("current", {}).get("weather_code", 0)
            condition_map = {
                0: "Sunny",
                1: "Partly Cloudy", 2: "Partly Cloudy", 3: "Partly Cloudy",
                45: "Foggy", 48: "Foggy",
                51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
                61: "Rainy", 63: "Rainy", 65: "Rainy",
                71: "Snowy", 73: "Snowy", 75: "Snowy",
                80: "Rain Showers", 81: "Rain Showers", 82: "Rain Showers",
                95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm"
            }
            condition = condition_map.get(code, "Sunny")
            
            current_temp = round(wdata.get("current", {}).get("temperature_2m", 25))
            humidity = wdata.get("current", {}).get("relative_humidity_2m", 60)
            wind_speed = round(wdata.get("current", {}).get("wind_speed_10m", 10))
            
            # Map 3-day forecast
            forecast = []
            daily = wdata.get("daily", {})
            times = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            codes = daily.get("weather_code", [])
            
            for i in range(min(3, len(times))):
                day_name = f"Day {i+1}"
                temp_val = round(temps[i]) if i < len(temps) else 25
                code_val = codes[i] if i < len(codes) else 0
                day_cond = condition_map.get(code_val, "Sunny")
                forecast.append({
                    "day": day_name,
                    "temp": temp_val,
                    "icon": "Sun" if "Sunny" in day_cond or "Clear" in day_cond else "Cloud" if "Cloudy" in day_cond or "Foggy" in day_cond else "Rain"
                })
            
            return {
                "currentTemp": current_temp,
                "condition": condition,
                "humidity": humidity,
                "windSpeed": wind_speed,
                "forecast": forecast
            }
    except Exception as e:
        print("Weather API fetch error:", e)
        return None

# =========================
# GENERATE TRIP PLAN
# =========================
@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}

    destination = data.get("destination", "").strip()
    days = int(data.get("days") or 3)
    budget = int(data.get("budget") or 25000)
    trip_style = data.get("tripType", "Adventure")
    travel_mode = data.get("travelMode", "Plane")

    # 1. Geocode destination & fetch weather (real-time weather!)
    dest_coords = geocode_cached(destination)
    lat = dest_coords["lat"] if dest_coords else 20.5306
    lng = dest_coords["lng"] if dest_coords else 70.7357
    weather = fetch_live_weather(lat, lng)

    # 2. Try Gemini AI generation
    ai_client = get_openai_client()
    if ai_client:
        prompt = f"""
        You are an expert travel assistant. Create a highly detailed, personalized, and realistic travel plan for a trip to {destination}.
        Trip details:
        - Duration: {days} days
        - Total Budget: INR {budget}
        - Travel Style: {trip_style}
        - Mode of Transport: {travel_mode}

        Provide the response in the following JSON format only. Do not include any explanation or markdown formatting (like ```json). The coordinates MUST be highly accurate, real coordinates in {destination} for each spot.

        {{
          "destination": "{destination.title()}",
          "description": "An engaging, personalized, and eco-friendly description of the trip.",
          "estimatedCost": {budget},
          "days": {days},
          "flightDetails": "Detailed information about transport, flights, or transit options.",
          "packingList": ["Passport", "Comfortable walking shoes", "Camera", "Local currency"],
          "hotels": [
            {{
              "name": "Eco Hotel/Resort Name",
              "location": "Neighborhood, {destination.title()}",
              "pricePerNight": 120,
              "rating": 4.8,
              "description": "Short details of this stay.",
              "coordinates": {{"lat": {lat + 0.005}, "lng": {lng - 0.005}}},
              "amenities": ["Wifi", "Green energy", "Recycling"]
            }}
          ],
          "itinerary": [
            {{
              "placeName": "Attraction Name",
              "category": "Monument/Nature/Museum/Leisure",
              "time": "Day 1 - Morning",
              "description": "Engaging details about what to see and explore.",
              "rating": "4.7",
              "coordinates": {{"lat": {lat + 0.01}, "lng": {lng + 0.01}}}
            }}
          ],
          "topPlaces": [
            {{
              "name": "Sight Spot Name",
              "location": "{destination.title()}",
              "description": "Brief description of the sight.",
              "rating": "4.5",
              "coordinates": {{"lat": {lat - 0.01}, "lng": {lng + 0.015}}},
              "amenities": ["Guided Tours", "Photography"]
            }}
          ],
          "insights": {{
            "crowdLevel": "Medium",
            "bestTimeToVisit": "October to March",
            "safetyStatus": "Very Safe",
            "travelAlert": "No active travel alerts."
          }}
        }}
        """
        try:
            res = ai_client.chat.completions.create(
                model=active_ai_model or "gemini-2.5-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            plan_str = res.choices[0].message.content.strip()
            
            # Clean possible markdown block formatting
            if plan_str.startswith("```"):
                lines = plan_str.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                plan_str = "\n".join(lines).strip()
            
            parsed_plan = json.loads(plan_str)
            if weather:
                parsed_plan["weatherForecast"] = weather
            
            return parsed_plan
        except Exception as e:
            print("Gemini AI plan generation failed, falling back to CSV:", e)

    # 3. CSV Fallback Logic
    filtered = []
    for place in places:
        city = place.get("city", place["name"]).lower()
        type_ = place["type"]
        if city == destination.lower():
            if type_.lower() == "city":
                continue
            flight = int(place["flight_cost"])
            train_cost = int(place["train_cost"])
            bus_cost = int(place["bus_cost"])
            train = train_cost if train_cost > 0 else 999999
            bus = bus_cost if bus_cost > 0 else 999999
            if travel_mode == "Plane" and flight > 0:
                transport_cost = flight
            elif travel_mode == "Train" and train_cost > 0:
                transport_cost = train
            elif travel_mode == "Bus" and bus_cost > 0:
                transport_cost = bus
            else:
                transport_cost = min(flight, train, bus)
            total_cost = transport_cost + int(place["hotel_per_day"]) * days
            if total_cost <= budget:
                filtered.append(place)

    if not filtered:
        for place in places:
            if place.get("city", "").lower() == destination.lower() and place["type"].lower() != "city":
                filtered.append(place)

    if not filtered:
        return {
            "destination": destination.title(),
            "plan": {},
            "error": "City not found"
        }

    priority = {
        "Monument": 1, "Historical": 2, "Religious": 3, "Beach": 4, 
        "Nature": 5, "Shopping": 6, "Leisure": 7, "Hotel": 8
    }

    def get_sort_key(x):
        type_priority = -1 if x["type"].lower() == trip_style.lower() else priority.get(x["type"], 9)
        return (
            -float(x["rating"]),
            type_priority,
            float(x["days"])
        )

    filtered.sort(key=get_sort_key)

    used = set()
    itinerary_items = []
    current_day = 1
    current_time = 0.0

    for place in filtered:
        name = place["name"]
        if name in used:
            continue

        place_time = float(place["days"])
        if place_time <= 0:
            continue

        if current_time + place_time > 1.0:
            current_day += 1
            current_time = 0.0

        if current_day > days:
            break

        used.add(name)

        image = place.get("image", "")
        desc = place.get("description", "")
        
        # Don't do heavy external API calls in the fallback loop to avoid 60s+ loading times!
        if not desc:
            desc = f"Visit {name} — a highly rated {place['type'].lower()} destination (⭐ {place['rating']})."

        # Fast pseudo-coordinates based on destination center to prevent geocoding timeouts
        spread = 0.015
        angle = (len(used) / len(filtered)) * 2 * 3.14159
        fake_lat = lat + 0.005 + (0.015 * (len(used) % 3))
        fake_lng = lng - 0.005 + (0.015 * (len(used) % 2))
        coords = {"lat": fake_lat, "lng": fake_lng}

        itinerary_items.append({
            "placeName": name,
            "category": place["type"],
            "time": f"Day {current_day}",
            "description": desc,
            "image": image,
            "rating": place["rating"],
            "coordinates": coords
        })

        current_time += place_time

    while current_day <= days:
        has_items = any(item["time"].startswith(f"Day {current_day}") for item in itinerary_items)
        if not has_items:
            itinerary_items.append({
                "placeName": "Explore Local Culture & Markets",
                "category": "Leisure",
                "time": f"Day {current_day}",
                "description": "Take a relaxing day to explore the neighborhood at your own pace.",
                "image": "https://placehold.co/400x300/1e293b/06b6d4?text=Leisure+Day",
                "rating": "4.5",
                "coordinates": {"lat": lat, "lng": lng}
            })
        current_day += 1

    top_places = []
    for item in itinerary_items[:4]:
        top_places.append({
            "name": item["placeName"],
            "location": destination.title(),
            "description": item["description"],
            "images": [item["image"]] if item["image"] else [],
            "peopleViewing": 15,
            "amenities": ["Guided Tours", "Photography"],
            "coordinates": item["coordinates"]
        })

    result_trip = {
        "destination": destination.title(),
        "description": f"Discover the best of {destination.title()} — a perfectly curated journey.",
        "estimatedCost": budget,
        "days": days,
        "flightDetails": f"Book transit/flight options to {destination.title()} in advance.",
        "packingList": ["Passport", "Comfortable walking shoes", "Camera", "Sunscreen"],
        "hotels": [
            {
                "name": f"Green Resort {destination.title()}",
                "location": f"Center, {destination.title()}",
                "pricePerNight": 110,
                "rating": 4.7,
                "description": "Comfortable eco-certified stay.",
                "coordinates": {"lat": lat - 0.003, "lng": lng + 0.003},
                "amenities": ["Green energy", "Organic food"]
            }
        ],
        "itinerary": itinerary_items,
        "topPlaces": top_places,
        "insights": {
            "crowdLevel": "Medium",
            "bestTimeToVisit": "October – March",
            "safetyStatus": "Very Safe"
        }
    }

    if weather:
        result_trip["weatherForecast"] = weather

    return result_trip


# =========================
# CHATBOT ASSISTANT ROUTE
# =========================
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    messages = data.get("messages", [])
    
    ai_client = get_openai_client()
    if ai_client:
        try:
            gpt_messages = [
                {"role": "system", "content": "You are VoyageAI, an expert and enthusiastic travel assistant. Give helpful, concise, and engaging travel advice. Use simple bullet points if necessary. Do NOT use markdown bold/italics unless necessary."}
            ]
            for i, msg in enumerate(messages):
                # Skip the initial static assistant message to avoid API validation errors
                if i == 0 and msg.get("role") == "assistant":
                    continue
                gpt_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            
            res = ai_client.chat.completions.create(
                model=active_ai_model or "gemini-2.5-flash",
                messages=gpt_messages,
                max_tokens=300
            )
            return {"reply": res.choices[0].message.content.strip()}
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower() or "RESOURCE_EXHAUSTED" in error_str:
                return {"reply": "⚠️ **AI Quota Exceeded:** I'm currently receiving too many requests and my AI brain is rate-limited by the provider (Gemini). Please try again in a minute, or check your API key quota!"}
            print("Chat API Error:", e)
            
    # Fallback smart chatbot logic
    last_msg = messages[-1]["content"].lower() if messages else ""
    
    if "hello" in last_msg or "hi" in last_msg or "hey" in last_msg:
        reply = "Hello there! I'm VoyageAI, your travel guide. 🌿 How can I help you plan your next trip?"
    elif "budget" in last_msg:
        reply = "Planning on a budget? Try Eastern Europe or beautiful spots in Asia like Bali or Goa. Tip: Travel off-season (Oct-Mar) and book hostels or homestays!"
    elif "beach" in last_msg or "sea" in last_msg:
        reply = "Love the beach? 🏖️ I highly recommend Bali (culture & sea), Goa (historic beaches), or the Maldives (luxury & crystal waters)!"
    elif "india" in last_msg or "goa" in last_msg or "delhi" in last_msg:
        reply = "Incredible India! 🇮🇳 I can recommend Delhi for street food and history, Agra for the Taj Mahal, and Goa for relaxing beaches and temples."
    elif "pack" in last_msg or "what to pack" in last_msg:
        reply = "Essential travel packing list:\n• Documents: Passport, visa, insurance\n• Clothes: Quick-dry layers, comfortable shoes\n• Tech: Universal adapter, power bank\n• Money: Small cash bills + international cards\n\nPro tip: Roll clothes to save space! 🎒"
    else:
        reply = "I'm VoyageAI, your smart nature-themed guide! 🌿 You can ask me about budget tips, beach destinations, or what to pack!"
        
    return {"reply": reply}

@app.errorhandler(404)
def not_found(e):
    return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5173)
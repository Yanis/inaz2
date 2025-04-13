import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime, timedelta, time # Ajout time
import logging
from flask_cors import CORS
import random

# Importer les utilitaires si le fichier existe
try:
    from utils import calculate_distance_km, estimate_travel_time_minutes, is_poi_open
except ImportError:
    # Fallback si utils.py n'existe pas (mettre des fonctions vides ou basiques ici)
    logging.warning("utils.py non trouvé, utilisation de fonctions fallback.")
    def calculate_distance_km(coord1, coord2): return 1.0 # Dummy
    def estimate_travel_time_minutes(coord1, coord2, average_speed_kmh=5): return 30 # Dummy
    def is_poi_open(poi, datetime_to_check): return True # Dummy (toujours ouvert)


load_dotenv()

app = Flask(__name__)
CORS(app)

# Configuration Supabase
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")

if not url or not key:
    raise ValueError("SUPABASE_URL et SUPABASE_KEY doivent être définis dans .env")

supabase: Client = create_client(url, key)

logging.basicConfig(level=logging.INFO)

# --- Algorithme de génération d'itinéraire ---
def generate_intelligent_circuit(city_name, duration_days, interests, budget_per_day, age_group):
    """Génère un circuit basé sur les préférences et contraintes."""
    try:
        # 1. Récupérer l'ID et les coordonnées de la ville
        city_response = supabase.table('cities').select('id, latitude, longitude').eq('name', city_name).limit(1).execute()
        if not city_response.data:
            return {"error": f"Ville '{city_name}' non trouvée dans la base de données."}
        city = city_response.data[0]
        city_id = city['id']
        city_coords = {'latitude': city['latitude'], 'longitude': city['longitude']}
        logging.info(f"Ville trouvée: {city_name} (ID: {city_id})")

        # 2. Récupérer les POIs pertinents pour cette ville
        # On filtre un peu, mais le gros du filtrage se fera en Python
        poi_query = supabase.table('pois').select('*').eq('city_id', city_id)

        # Filtrage par intérêts (si fournis) - 'cs' pour 'contains' sur array de text
        if interests:
             poi_query = poi_query.overlaps('interest_tags', interests) # overlaps est mieux que cs pour les tableaux

        poi_response = poi_query.execute()
        if not poi_response.data:
            return {"error": f"Aucun point d'intérêt trouvé pour la ville '{city_name}' correspondant aux critères initiaux."}

        all_pois = poi_response.data
        logging.info(f"{len(all_pois)} POIs récupérés pour {city_name} après filtre intérêts.")

        # 3. Filtrer davantage en Python (plus flexible)
        # Ici, on pourrait filtrer par budget (cost_level), age_group (suitable_for_kids), etc.
        # Pour l'instant, on garde le filtre par intérêt simple
        relevant_pois = [
            poi for poi in all_pois
            if poi.get('latitude') and poi.get('longitude') # S'assurer qu'on a les coordonnées
            # Ajout possible : and (poi.get('cost_level') is None or poi.get('cost_level') * 10 <= budget_per_day) # Exemple très simple de filtre budget
        ]

        if not relevant_pois:
            return {"error": "Aucun POI pertinent trouvé après filtrage détaillé."}

        logging.info(f"{len(relevant_pois)} POIs pertinents après filtrage Python.")

        # --- Génération de l'itinéraire jour par jour ---
        itinerary = []
        # Copie pour pouvoir supprimer les POIs utilisés sans affecter la liste originale
        available_pois = list(relevant_pois)
        random.shuffle(available_pois) # Un peu d'aléatoire pour varier les départs

        current_date = datetime.now().date() # Date de départ (pour les horaires)

        for day_num in range(1, duration_days + 1):
            daily_schedule = []
            # Définir les heures de début et fin de journée (approximatif)
            start_time_of_day = datetime.combine(current_date, time(9, 0)) # Commence à 9h
            end_time_of_day = datetime.combine(current_date, time(18, 0)) # Finit vers 18h (peut être ajusté)
            current_time = start_time_of_day
            last_poi_location = city_coords # Départ du centre-ville (ou hôtel si on avait l'info)

            # Temps max par jour en minutes
            max_daily_duration_minutes = (end_time_of_day - start_time_of_day).total_seconds() / 60

            pois_added_today = 0
            day_visit_duration = 0

            # Tant qu'il reste du temps dans la journée et des POIs disponibles
            while current_time < end_time_of_day and available_pois and day_visit_duration < max_daily_duration_minutes:
                best_poi = None
                min_travel_time = float('inf')
                pois_to_remove_if_unsuitable = [] # Pour les POIs qu'on ne peut pas visiter aujourd'hui

                # Trier les POIs restants par proximité par rapport au dernier lieu visité
                available_pois.sort(key=lambda p: calculate_distance_km(last_poi_location, p))

                # Chercher le prochain POI convenable
                for poi in available_pois:
                    poi_coords = {'latitude': poi['latitude'], 'longitude': poi['longitude']}
                    travel_time = estimate_travel_time_minutes(last_poi_location, poi_coords)
                    estimated_arrival_time = current_time + timedelta(minutes=travel_time)
                    visit_duration = poi.get('avg_visit_duration_minutes', 60) # Durée par défaut: 60 min

                    # Vérifier si on a le temps pour trajet + visite avant la fin de journée
                    if estimated_arrival_time + timedelta(minutes=visit_duration) > end_time_of_day:
                         pois_to_remove_if_unsuitable.append(poi) # Trop tard pour celui-ci aujourd'hui
                         continue # Essayer le suivant

                    # Vérifier les horaires d'ouverture
                    if not is_poi_open(poi, estimated_arrival_time):
                         pois_to_remove_if_unsuitable.append(poi) # Fermé à l'arrivée prévue
                         logging.info(f"POI '{poi['name']}' fermé à {estimated_arrival_time.strftime('%H:%M')}, essai suivant.")
                         continue # Essayer le suivant

                    # Si on arrive ici, le POI est candidat
                    best_poi = poi
                    break # On prend le plus proche qui convient

                # Retirer les POIs qu'on ne pouvait pas visiter aujourd'hui de la liste pour cette journée
                # (ils pourraient être ouverts un autre jour ou plus tôt)
                # for p_rem in pois_to_remove_if_unsuitable:
                #     if p_rem in available_pois:
                #         available_pois.remove(p_rem) # Attention : peut supprimer définitivement

                if best_poi:
                    # Ajouter le POI sélectionné à l'itinéraire du jour
                    travel_time = estimate_travel_time_minutes(last_poi_location, best_poi) # Recalculer au cas où
                    estimated_arrival_time = current_time + timedelta(minutes=travel_time)
                    visit_duration = best_poi.get('avg_visit_duration_minutes', 60)
                    departure_time = estimated_arrival_time + timedelta(minutes=visit_duration)

                    daily_schedule.append({
                        "poi_id": best_poi['id'],
                        "name": best_poi['name'],
                        "type": best_poi['poi_type'],
                        "latitude": best_poi['latitude'],
                        "longitude": best_poi['longitude'],
                        "estimated_travel_minutes": round(travel_time),
                        "estimated_arrival": estimated_arrival_time.strftime('%H:%M'),
                        "visit_duration_minutes": visit_duration,
                        "estimated_departure": departure_time.strftime('%H:%M')
                    })

                    logging.info(f"Jour {day_num}: Ajout de '{best_poi['name']}' - Arrivée: {estimated_arrival_time.strftime('%H:%M')}, Départ: {departure_time.strftime('%H:%M')}")

                    # Mettre à jour l'état pour la prochaine itération
                    current_time = departure_time
                    last_poi_location = best_poi # Mettre à jour les coordonnées
                    available_pois.remove(best_poi) # Ce POI est visité
                    pois_added_today += 1
                    day_visit_duration += travel_time + visit_duration

                else:
                    # Aucun POI convenable trouvé pour le reste de la journée
                    logging.info(f"Jour {day_num}: Plus aucun POI convenable trouvé pour aujourd'hui.")
                    break # Fin de la journée

            if daily_schedule: # Seulement ajouter le jour s'il y a des activités
                 itinerary.append({
                    "day": day_num,
                    "activities": daily_schedule
                 })
            else:
                 logging.warning(f"Jour {day_num}: Aucune activité planifiée.")
                 # On pourrait ajouter une activité "temps libre" ou simplement ne pas ajouter le jour

            current_date += timedelta(days=1) # Passer au jour suivant

        if not itinerary:
             return {"error": "Impossible de générer un itinéraire avec les POIs disponibles et les contraintes."}

        return {"itinerary": itinerary}

    except Exception as e:
        logging.exception("Erreur lors de la génération du circuit:")
        return {"error": f"Une erreur serveur est survenue: {e}"}

# --- Route API ---
@app.route('/generate_circuit', methods=['POST'])
def handle_generate_circuit():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.get_json()
    logging.info(f"Requête reçue: {data}")

    # Récupérer les données de la requête
    city_name = data.get('city')
    duration_str = data.get('duration', '1 jour') # Ex: "3 jours"
    age_group = data.get('age')
    interests = data.get('interests', []) # Doit être une liste de strings
    budget_str = data.get('budget', '80') # Ex: "80"

    # Validation basique
    if not city_name or city_name == 'Sélectionnez une ville':
        return jsonify({"error": "Veuillez sélectionner une ville valide."}), 400

    try:
        # Extraire le nombre de jours
        duration_days = int(duration_str.split(' ')[0]) if isinstance(duration_str, str) else int(duration_str)
        if duration_days <= 0: raise ValueError()
    except (ValueError, IndexError):
         # Essayer une conversion directe si c'est déjà un nombre
         try:
             duration_days = int(duration_str)
             if duration_days <= 0: raise ValueError()
         except ValueError:
            return jsonify({"error": "Durée invalide."}), 400


    try:
        budget_per_day = int(budget_str)
    except ValueError:
        return jsonify({"error": "Budget invalide."}), 400

    # Appeler la logique principale
    result = generate_intelligent_circuit(
        city_name=city_name,
        duration_days=duration_days,
        interests=interests,
        budget_per_day=budget_per_day,
        age_group=age_group
    )

    if "error" in result:
        return jsonify(result), 400 # Ou 500 si erreur serveur interne
    else:
        # Calculer les statistiques globales ici si nécessaire
        total_places = sum(len(day.get('activities', [])) for day in result.get('itinerary', []))
        # total_distance, total_time seraient plus complexes à calculer précisément ici
        # On peut retourner les données brutes et laisser le frontend calculer ou simplifier

        response_data = {
            "summary": {
                "city": city_name,
                "duration": f"{duration_days} jour{'s' if duration_days > 1 else ''}",
                "budget": f"€{budget_per_day}/jour",
                "interests": ", ".join(interests) if interests else "Aucun"
            },
            "stats": {
                 # Ces stats sont maintenant basées sur l'itinéraire réel
                "places": total_places,
                # Les estimations de distance/temps total peuvent être ajoutées ici
                # "distance_km": calculate_total_distance(result['itinerary']),
                # "time_hours": calculate_total_time(result['itinerary']),
            },
            "itinerary": result['itinerary']
        }
        return jsonify(response_data), 200

# Pour lancer l'application Flask en local
if __name__ == '__main__':
    app.run(debug=True, port=5000) # debug=True pour le développement
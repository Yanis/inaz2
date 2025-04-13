import math
from datetime import datetime, time, timedelta
from geopy.distance import geodesic
from dateutil import parser as date_parser
import logging # Pour le débogage

logging.basicConfig(level=logging.INFO)

def calculate_distance_km(coord1, coord2):
    """Calcule la distance en km entre deux points (latitude, longitude)."""
    if not coord1 or not coord2:
        return float('inf')
    try:
        # geopy attend (latitude, longitude)
        return geodesic((coord1['latitude'], coord1['longitude']), (coord2['latitude'], coord2['longitude'])).km
    except Exception as e:
        logging.error(f"Erreur calcul distance entre {coord1} et {coord2}: {e}")
        return float('inf')

def estimate_travel_time_minutes(coord1, coord2, average_speed_kmh=5):
    """Estime le temps de trajet en minutes (très basique : marche/transport en commun lent)."""
    distance = calculate_distance_km(coord1, coord2)
    if distance == float('inf'):
        return float('inf')
    # Convertit la vitesse en km/minute
    speed_kmm = average_speed_kmh / 60
    if speed_kmm == 0:
        return float('inf')
    # Ajoute un temps fixe de 'changement/attente' pour chaque trajet
    fixed_time = 10 # minutes
    travel_time = (distance / speed_kmm) + fixed_time
    return travel_time

# --- Logique d'ouverture/fermeture (Simplifiée) ---
# Ceci est une version TRES simplifiée et doit être adaptée à TON format JSON
# Elle ne gère pas les jours fériés, les formats complexes, etc.
def is_poi_open(poi, datetime_to_check):
    """Vérifie si un POI est ouvert à une date/heure donnée. Adaptation nécessaire !"""
    if not poi.get('opening_hours'):
        logging.warning(f"Pas d'horaires pour {poi.get('name')}")
        return False # Prudence : considérer fermé si pas d'info

    opening_hours = poi['opening_hours']
    day_of_week = datetime_to_check.strftime('%a').lower() # ex: 'mon', 'tue'
    current_time = datetime_to_check.time()

    # Essayer de trouver un horaire spécifique pour ce jour
    day_schedule = opening_hours.get(day_of_week)

    # Si pas d'horaire spécifique, chercher un horaire générique
    if day_schedule is None:
        if opening_hours.get("all_days"):
            day_schedule = opening_hours.get("all_days")
        elif opening_hours.get("all_year"): # Moins précis
             day_schedule = opening_hours.get("all_year")
        # Ajouter la gestion 'seasonal' ici si nécessaire
        # elif opening_hours.get("seasonal"): ...

    if day_schedule is None or day_schedule == 'null' or day_schedule == '':
         logging.info(f"{poi.get('name')} fermé ce jour ({day_of_week})")
         return False # Fermé ce jour

    # Gérer le format "HH:MM-HH:MM" (simpliste)
    if isinstance(day_schedule, str) and '-' in day_schedule:
        try:
            open_str, close_str = day_schedule.split('-')
            # Utiliser dateutil.parser pour plus de flexibilité
            open_time = date_parser.parse(open_str).time()
            close_time = date_parser.parse(close_str).time()

            # Gérer le cas où ça ferme après minuit (ex: 09:00-01:00)
            if close_time < open_time:
                # Ouvert si: heure >= open OU heure < close (pour le jour suivant)
                # Attention: cette logique est simpliste si l'heure de vérif est après minuit
                 return current_time >= open_time or current_time < close_time # Ne gère pas correctement le changement de jour
            else:
                 # Cas normal
                 return open_time <= current_time < close_time

        except Exception as e:
            logging.error(f"Erreur parsing horaire simple '{day_schedule}' pour {poi.get('name')}: {e}")
            return False # Erreur de format = considérer fermé

    # Gérer le format liste de plages [{ "open": "HH:MM", "close": "HH:MM" }, ...]
    elif isinstance(day_schedule, list):
        try:
            for schedule_block in day_schedule:
                open_time = date_parser.parse(schedule_block['open']).time()
                close_time = date_parser.parse(schedule_block['close']).time()
                if close_time < open_time: # Gère fermeture après minuit
                     if current_time >= open_time or current_time < close_time:
                         return True
                elif open_time <= current_time < close_time:
                    return True
            return False # Pas dans une des plages horaires
        except Exception as e:
             logging.error(f"Erreur parsing horaire complexe '{day_schedule}' pour {poi.get('name')}: {e}")
             return False

    logging.warning(f"Format horaire non reconnu: {day_schedule} pour {poi.get('name')}")
    return False # Format inconnu
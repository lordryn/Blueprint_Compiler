import json
import re
from bs4 import BeautifulSoup


def process_blueprints(input_filename, output_filename):
    # Load the scraped HTML data
    with open(input_filename, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')

    # The new raw data uses a card grid format ('fab-card') which contains the exact amounts and slots
    # We find all main card containers
    cards = soup.find_all('div', class_=re.compile(r'^fab-card\b'))

    blueprints = []

    for card in cards:
        # Skip if it's a sub-element, ensure it has a header
        header = card.find('div', class_='fab-card-header')
        if not header:
            continue

        # Extract Blueprint Name
        name_span = header.find('span', class_='fab-card-name')
        if not name_span:
            continue
        name_text = name_span.text.replace('●', '').strip()

        # Extract Category
        type_span = header.find('span', class_='fab-card-type-badge')
        raw_type = type_span.get('title', 'other').lower() if type_span else 'other'

        # Expanded Categorization Logic for Star Citizen Types
        if any(keyword in raw_type for keyword in ['weapon', 'ammo', 'missile', 'bomb', 'torpedo', 'turret', 'fps']):
            category = "Weapon"
        elif any(keyword in raw_type for keyword in ['ship']):
            category = "Ship"
        elif any(keyword in raw_type for keyword in ['vehicle', 'ground', 'rover', 'hover']):
            category = "Vehicle"
        elif any(keyword in raw_type for keyword in
                 ['armour', 'shield', 'quantum', 'cooler', 'power', 'component', 'drive', 'mining', 'salvage',
                  'tractor']):
            category = "Component"
        else:
            category = "Other"

        # Extract individual stats from the card
        mfr = ""
        mfr_span = card.find('span', class_='fab-card-mfr')
        if mfr_span:
            mfr = mfr_span.get('title', mfr_span.text).strip()

        size = ""
        size_span = card.find('span', class_='fab-card-size')
        if size_span:
            size = size_span.text.replace('S', '').strip()

        # Grade might not be present in the card view, but we check just in case
        grade = ""
        grade_span = card.find('span', class_='fab-card-grade')
        if grade_span:
            grade = grade_span.text.strip()

        # Extract highly detailed Materials (Slots + Amounts)
        mats = []
        slots = card.find_all('span', class_='fab-card-slot')

        for slot in slots:
            slot_name_el = slot.find('span', class_='fab-card-slot-name')
            mat_el = slot.find('span', class_='fab-card-slot-mat')

            if mat_el:
                slot_name = slot_name_el.text.strip() if slot_name_el else "Part"
                mat_full_text = mat_el.text.strip()

                # Default values
                amount = "Unknown"
                name = mat_full_text

                # Extract amount from parenthesis e.g., "Titanium (2 SCU)" or "Janalite (×1)"
                match = re.search(r'\((.*?)\)', mat_full_text)
                if match:
                    amount = match.group(1).replace('×', '').strip()  # Clean up 'x' multipliers
                    name = mat_full_text[:match.start()].strip()  # Everything before the parenthesis

                mats.append({
                    "slot": slot_name,
                    "name": name,
                    "amount": amount,
                    # This matches the structure of your example JSON (e.g., "Frame: Taranite (0.06 SCU)")
                    "formatted": f"{slot_name}: {mat_full_text}"
                })

        # Extract Crafting Time
        crafting_time = ""
        time_span = card.find('span', class_='fab-card-time')
        if time_span:
            crafting_time = time_span.text.replace('⏱', '').strip()

        # Build final dictionary entry
        entry = {
            "member_name": "Org Armory",
            "blueprint_name": name_text,
            "category": category,
            "manufacturer": mfr,
            "grade": grade,
            "size": size,
            "materials": mats,
            "crafting_time": crafting_time
        }

        blueprints.append(entry)

    # Save directly to the Flask app's data file
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(blueprints, f, indent=4)

    print(f"Successfully compiled {len(blueprints)} blueprints into {output_filename}!")


if __name__ == '__main__':
    process_blueprints('blueprints unprocessed.txt', 'blueprints.json')
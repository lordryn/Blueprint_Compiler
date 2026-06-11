import json
import os
import re
from bs4 import BeautifulSoup

def process_blueprints(raw_path="blueprints unprocessed.txt", output_path="blueprints.json"):
    """
    Parses the raw HTML fabricator blueprint cards and outputs a clean blueprints.json file.
    """
    if not os.path.exists(raw_path):
        return False, f"Parser error: Raw catalog file '{raw_path}' not found. Please run grabber first."

    try:
        with open(raw_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')
        cards = soup.find_all('div', class_=re.compile(r'^fab-card\b'))

        compiled_blueprints = []

        for card in cards:
            header = card.find('div', class_='fab-card-header')
            if not header:
                continue

            # Extract Blueprint Name
            name_span = header.find('span', class_='fab-card-name')
            if not name_span:
                continue
            blueprint_name = name_span.text.replace('●', '').strip()

            # Extract Category
            type_span = header.find('span', class_='fab-card-type-badge')
            raw_type = type_span.get('title', 'other').lower() if type_span else 'other'

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

            # Determine manufacturer
            mfr_el = card.find('span', class_='fab-card-mfr')
            manufacturer = mfr_el.get('title', mfr_el.text).strip() if mfr_el else ""

            # Determine size
            size_el = card.find('span', class_='fab-card-size')
            size = ""
            if size_el:
                size = size_el.text.replace('S', '').strip()

            # Grade might not be present in the card view, but we check just in case
            grade = ""
            grade_span = card.find('span', class_='fab-card-grade')
            if grade_span:
                grade = grade_span.text.strip()

            # Determine crafting time
            time_el = card.find('span', class_='fab-card-time')
            crafting_time = ""
            if time_el:
                crafting_time = time_el.text.replace('⏱', '').strip()

            # Process materials
            materials_list = []
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

                    # Extract amount from parenthesis
                    match = re.search(r'\((.*?)\)', mat_full_text)
                    if match:
                        amount = match.group(1).replace('×', '').strip()
                        name = mat_full_text[:match.start()].strip()

                    materials_list.append({
                        "slot": slot_name,
                        "name": name,
                        "amount": amount,
                        "formatted": f"{slot_name}: {mat_full_text}"
                    })

            compiled_item = {
                "member_name": "Org Armory",
                "blueprint_name": blueprint_name,
                "category": category,
                "manufacturer": manufacturer,
                "grade": grade,
                "size": size,
                "materials": materials_list,
                "crafting_time": crafting_time
            }
            compiled_blueprints.append(compiled_item)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(compiled_blueprints, f, indent=4)

        return True, f"Successfully parsed and compiled {len(compiled_blueprints)} blueprints into {output_path}."
    except Exception as e:
        return False, f"Parser exception: {str(e)}"

def parse_and_compile(raw_path="blueprints unprocessed.txt", output_path="blueprints.json"):
    """
    Wrapper for backward compatibility.
    """
    return process_blueprints(raw_path, output_path)

if __name__ == "__main__":
    success, msg = parse_and_compile()
    print(msg)

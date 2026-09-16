#include <stdbool.h>

bool check_medication_pause(int age, bool is_diabetic, bool is_anticoagulant, int gfr, int hours_since_last_dose, bool is_emergency_surgery) {
  if (is_anticoagulant && (gfr < 30 || (age > 75 && is_diabetic && is_emergency_surgery == false))) {
    return true;
  }
  if (age > 75 && is_diabetic && is_anticoagulant && hours_since_last_dose < 12) {
    return true;
  }
  return false;
}
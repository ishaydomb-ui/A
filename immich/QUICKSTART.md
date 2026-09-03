# Immich - התקנה מהירה

## דרישות מקדימות

- Docker Engine ו-Docker Compose מותקנים
- לפחות 6GB RAM (מומלץ 8GB)
- מינימום 2 CPU cores
- מקום פנוי בדיסק (תלוי בגודל הספריה)

---

## שלב 1: הורדת הקבצים

```bash
# אם עדיין לא קיימת תיקייה immich
mkdir -p /opt/immich
cd /opt/immich
```

העתק את הקבצים הבאים לתיקייה:
- `docker-compose.yml`
- `.env.example`
- `install.sh`
- `backup.sh`

---

## שלב 2: הגדרת סביבה

```bash
# העתק את קובץ ה-environment
cp .env.example .env

# עדכן את הנתיבים לפי המערכת שלך:
nano .env
```

שנה את הערכים הבאים ב-.env:

```env
# מקום שמירת התמונות (דיסק גדול/NAS)
UPLOAD_LOCATION=/mnt/photos

# מקום נתונים של מסד הנתונים (חייב להיות SSD מקומי!)
DB_DATA_LOCATION=./postgres

# אזור זמן (ישראל)
TZ=Asia/Jerusalem
```

---

## שלב 3: הרשה ביצוע עבור הסקריפט

```bash
chmod +x install.sh
chmod +x backup.sh
```

---

## שלב 4: הפעלה ראשונית

```bash
# הסקריפט יבדוק את ההגדרות ויהפעיל את הקונטיינרים
./install.sh
```

סקריפט זה:
- ✅ בודק את Docker
- ✅ בודק זיכרון RAM
- ✅ יוצר תיקיות נדרשות
- ✅ אם DB_LOCATION בנתיב רשת - עוצר עם שגיאה ⚠️
- ✅ הורד תמונות Docker
- ✅ מפעיל את כל הקונטיינרים

---

## שלב 5: גישה לממשק

```bash
# בדוק שכל הקונטיינרים רצים
docker compose ps

# ממשק אינטרנט
http://localhost:2283
```

פרטי התחברות ראשוניים:
- **דוא"ל**: admin@example.com
- **סיסמה**: password

**שנה סיסמה באופן מיידי!**

---

## שלב 6: הוסף טלפון שלך

בממשק הווב:
1. Settings → Account → Devices
2. יצור Mobile Auth Link
3. סרוק QR-קוד באיישי ב-Immich App
4. בחר תיקיה לגיבוי (תמונות או All)

---

## שלב 7: הגדר גיבויים אוטומטיים

```bash
# ערוך את crontab
crontab -e

# הוסף שורה זו (גיבוי יומי בשעה 04:00):
0 4 * * * cd /opt/immich && ./backup.sh >> /var/log/immich-backup.log 2>&1
```

---

## טיפים חשובים

### ⚠️ דרישה קריטית לבסיס הנתונים
```
DB_DATA_LOCATION חייב להיות על דיסק SSD מקומי - לא NAS!
אם תשתמש ברשת: מסד הנתונים יתמוטט וההתקנה תישמד.
```

### 📁 אחסון תמונות
UPLOAD_LOCATION יכול להיות:
- דיסק מקומי גדול
- NAS (טוב לגיבוי)
- דיסק חיצוני

### 🎥 טיוב וידאו (אופציונלי)
```bash
# אם יש לך GPU, עדכן את docker-compose.yml
# העתק קטעים מתוך hwaccel.transcoding.yml
```

---

## פתרון בעיות

### הקונטיינרים לא מתחילים

```bash
# בדוק יומנים
docker compose logs immich-server

# אם יש שגיאה ברשת: בדוק חיבור
docker compose logs immich-postgres
```

### בעיה: "Database is corrupt"

**סימן**: DB_DATA_LOCATION היה על NAS

**פתרון**:
1. עצור: `docker compose down -v`
2. מחק את `./postgres`
3. העבר DB_DATA_LOCATION ל-SSD
4. הפעל מחדש: `docker compose up -d`

### יומנים גדולים מדי

```bash
# נקה יומנים ישנים
docker system prune -a --volumes
```

---

## הפעלה/עצירה

```bash
# עצור את כל הקונטיינרים
cd /opt/immich
docker compose down

# הפעל מחדש
docker compose up -d

# בדוק סטטוס
docker compose ps
```

---

## העדכון למהדורה חדשה

```bash
cd /opt/immich

# הוצא גיבוי
./backup.sh

# עדכן
docker compose pull
docker compose up -d

# בדוק את הרישומים
docker compose logs -f immich-server
```

---

## קישורים שימושיים

- 📖 [תיעוד Immich](https://immich.app/docs/)
- 🐳 [Docker Hub](https://hub.docker.com/r/ghcr.io/immich-app)
- 💬 [דיסקורד Immich](https://discord.gg/D8JsnBEuKb)

---

**כל שאלה? בדוק את `README.md` לפרטים מלאים.**

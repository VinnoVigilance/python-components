import os
import json
from psycopg2.extras import execute_values, Json
from database.dbConfig import connection_pool
from database.hashingFilePath import calculate_file_hash, calculate_record_hash

from datetime import datetime, date



def insert_raw_watchlist_file(
    source_name,
    url,
    file_path,
    file_type,
    downloaded_at,
    schedule,
):
    file_hash = calculate_file_hash(file_path)
    file_size = os.path.getsize(file_path)
    version = "1.0.0"
    status = "Downloaded"

    conn = connection_pool.getconn()
    cur = conn.cursor()



    query_id = """
        SELECT id
        FROM raw.lkup_source_list
        WHERE authority = %s
    """



    cur.execute(query_id, (source_name,))
    source_id = cur.fetchone()

    if source_id is None:
        raise ValueError(f"Source '{source_name}' not found in raw.lkup_source_list")
    
    source_id = source_id[0]
    

    #code = "sdn"
    query_type_id = """
    SELECT id
    FROM raw.lkup_source_list_type
    WHERE source_id = %s 
"""
    cur.execute(query_type_id, (source_id,))
    list_type_id = cur.fetchone()

    if list_type_id is None:
        raise ValueError(f"Source '{list_type_id}' not found in raw.lkup_source_list")
    
    list_type_id = list_type_id[0]


    query = """
        INSERT INTO raw.watchlist_file
        (
            source_id,
            list_type_id,
            source_url,
            storage_path,
            file_type,
            file_size,
            file_hash,
            version,
            downloaded_at,
            status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    """

    cur.execute(
        query,
        (
            source_id,
            list_type_id,
            url,
            file_path,
            file_type,
            file_size,
            file_hash,
            version,
            downloaded_at,
            status
        )
    )

    file_id = cur.fetchone()[0]

    conn.commit()

    cur.close()
    connection_pool.putconn(conn)

    return {"file_id" : file_id, "source_id" : source_id, "list_type_id" : list_type_id}

def insert_raw_unparsed_watchlist_payload(file_id, file_path):
   conn = connection_pool.getconn()
   cur = conn.cursor()

   records = []
   with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line: 
                json_obj = json.loads(line)            
                #records.append((json.dumps(json_obj),))
                records.append((file_id, json.dumps(json_obj)))            
                # records.append((json_obj['field1'], json_obj['field2']))

   query = """
   INSERT INTO raw.unparsed_watchlist_payload (file_id,raw_json) VALUES %s RETURNING raw_json;
"""
   execute_values(cur, query, records)
   raw_json = cur.fetchone()[0]


   conn.commit()
   cur.close()
   connection_pool.putconn(conn)


   return raw_json


def insert_per_raw_unparsed_watchlist_payload(file_id, source_name,raw_json, created_at):

    conn = connection_pool.getconn()
    cur = conn.cursor()

    try:
        query = """
            INSERT INTO raw.unparsed_watchlist_payload
            (
                file_id,
                raw_json
            )
            VALUES (%s, %s)
            RETURNING raw_json;
        """

        if isinstance(raw_json, str):
            raw_json = json.loads(raw_json)

        cur.execute(query, (file_id, Json(raw_json)))
        raw_json = cur.fetchone()[0]

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        connection_pool.putconn(conn)
    
    return raw_json

def insert_staging_watchlist_record_staging (file_id, source_id, list_type_id, raw_json, created_at, external_id):
    record_hash = calculate_record_hash(raw_json)

    conn = connection_pool.getconn()
    cur = conn.cursor()
    
    obj =raw_json

    extracted_name = "None"

    entity_type = obj["entity_type"]
    print(raw_json)
    print(entity_type)
    query_entity_id = """
        SELECT id
        FROM raw.lkup_entity_type
        WHERE name = upper(%s)
    """

    cur.execute(query_entity_id, (entity_type,))
    entity_id = cur.fetchone()

    if entity_id is None:
        raise ValueError(f"Source '{entity_id}' not found in raw.lkup_entity_type")

    #entity_id = entity_id[0]
    



    query = """
        INSERT INTO staging.watchlist_record_staging
        (
            raw_file_id,
            source_id,
            list_type_id,
            external_id,
            entity_type_id,
            record_hash,
            valid_from,
            valid_to,

            
            extracted_name,
            full_payload,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;        
    """


    cur.execute(
        query,
        (
            file_id,
            source_id,
            list_type_id,
            external_id,
            entity_id,
            record_hash,
            datetime.now(),
            date(2050, 1, 1),
            

            extracted_name,
            Json(raw_json),
            created_at
        )
    )

    stage_id = cur.fetchone()[0]

    conn.commit()

    cur.close()
    connection_pool.putconn(conn)


    return stage_id




if __name__ == "__main__":
 file_path = "C:\\Users\\Administrator\\Desktop\\VV_Python_Project\\data\\final\\DNFBP_final.jsonl"

 reuslt = insert_raw_watchlist_file(
    "OFAC",
    "HTTPS://TST.COM",
    file_path,
    "XML",
    datetime.now(),
    "Monthly",
 )

 file_id = reuslt["file_id"]
 source_id = reuslt["source_id"]
 list_type_id = reuslt["list_type_id"]
 raw_json = insert_raw_unparsed_watchlist_payload(file_id, file_path)
 #print(raw_json)
 insert_staging_watchlist_record_staging (file_id, source_id, list_type_id, "TST", "VESSEL", "TST", raw_json, datetime.now())
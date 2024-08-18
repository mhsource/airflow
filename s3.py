import json
import boto3
import csv
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.hooks.base import BaseHook
from datetime import datetime, timedelta
import os

# Função para baixar o arquivo do S3
def download_file_from_s3posicional(s3_config, **kwargs):
    bucket_name = s3_config['bucket_name']
    key = s3_config['key']
    local_path = s3_config['local_path']
    
    aws_conn = BaseHook.get_connection('aws_default')  # Use o ID da conexão configurada
    session = boto3.Session(
        aws_access_key_id=aws_conn.login,
        aws_secret_access_key=aws_conn.password,
        region_name=aws_conn.extra_dejson.get('region_name', 'us-east-1')
    )
    s3 = session.client('s3')
    s3.download_file(bucket_name, key, local_path)
    print(f"Downloaded {key} from {bucket_name} to {local_path}")

# Função para processar o arquivo posicional
def process_file(local_path, origin_parameters, **kwargs):
    processed_data_list = []

    with open(local_path, 'r') as file:
        lines = file.readlines()

    for line in lines:
        processed_data = {}
        for nome, positions in origin_parameters.items():
            inicio = int(positions['inicio'])
            fim = int(positions['fim'])
            processed_data[nome] = line[inicio:fim].strip()
        processed_data_list.append(processed_data)

    return processed_data_list

# Função para enviar dados para SQS
def send_to_sqs(sqs_config, **kwargs):
    processed_data_list = kwargs['ti'].xcom_pull(task_ids='process_file')
    
    aws_conn = BaseHook.get_connection('aws_default')
    session = boto3.Session(
        aws_access_key_id=aws_conn.login,
        aws_secret_access_key=aws_conn.password,
        region_name=aws_conn.extra_dejson.get('region_name', 'us-east-1')
    )
    sqs = session.client('sqs')
    queue_url = sqs.get_queue_url(QueueName=sqs_config['queue_name'])['QueueUrl']

    for processed_data in processed_data_list:
        response = sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(processed_data)
        )
        print(f"Message sent to SQS: {processed_data}")
        print(f"SQS Response: {response}")

# Função para enviar dados para DynamoDB
def send_to_dynamodb(dynamodb_config, **kwargs):
    processed_data_list = kwargs['ti'].xcom_pull(task_ids='process_file')
    
    aws_conn = BaseHook.get_connection('aws_default')
    session = boto3.Session(
        aws_access_key_id=aws_conn.login,
        aws_secret_access_key=aws_conn.password,
        region_name=aws_conn.extra_dejson.get('region_name', 'us-east-1')
    )
    dynamodb = session.client('dynamodb')
    table_name = dynamodb_config['table']

    for processed_data in processed_data_list:
        dynamodb_item = {k: {'S': v} for k, v in processed_data.items()}
        response = dynamodb.put_item(
            TableName=table_name,
            Item=dynamodb_item
        )
        print(f"Item sent to DynamoDB table {table_name}: {processed_data}")
        print(f"DynamoDB Response: {response}")

# Função para salvar dados no S3 no formato JSON
def save_to_s3json(s3_config, s3json_config, **kwargs):
    processed_data_list = kwargs['ti'].xcom_pull(task_ids='process_file')
    
    lines_output_json = []
    
    for processed_data in processed_data_list:
        json_data = {}
        for param in s3json_config['parameters']:
            json_data[param['destiny']] = processed_data[param['origin']]
        lines_output_json.append(json_data)

    output_json_file_path = s3_config['local_path'].replace('.txt', '.json')
    with open(output_json_file_path, 'w') as output_file:
        json.dump(lines_output_json, output_file, indent=4)

    aws_conn = BaseHook.get_connection('aws_default')
    session = boto3.Session(
        aws_access_key_id=aws_conn.login,
        aws_secret_access_key=aws_conn.password,
        region_name=aws_conn.extra_dejson.get('region_name', 'us-east-1')
    )
    s3 = session.client('s3')
    s3.upload_file(output_json_file_path, s3_config['bucket_name'], s3_config['key'].replace('.txt', '.json'))
    print(f"Uploaded processed JSON file to S3: {s3_config['bucket_name']}/{s3_config['key'].replace('.txt', '.json')}")

# Função para salvar dados no S3 no formato CSV para Athena
def save_to_s3csvathena(s3_config, s3csvathena_config, origin_parameters, **kwargs):
    processed_data_list = kwargs['ti'].xcom_pull(task_ids='process_file')
    
    output_csv_athena_path = s3_config['local_path'].replace('.txt', '_athena.csv')
    
    with open(output_csv_athena_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        header = list(origin_parameters.keys())
        writer.writerow(header)
        for processed_data in processed_data_list:
            writer.writerow([processed_data[col] for col in origin_parameters])

    aws_conn = BaseHook.get_connection('aws_default')
    session = boto3.Session(
        aws_access_key_id=aws_conn.login,
        aws_secret_access_key=aws_conn.password,
        region_name=aws_conn.extra_dejson.get('region_name', 'us-east-1')
    )
    s3 = session.client('s3')
    athena_s3_key = s3_config['key'].replace('.txt', '_athena.csv')
    s3.upload_file(output_csv_athena_path, s3_config['bucket_name'], athena_s3_key)
    print(f"Uploaded processed CSV file for Athena to S3: {s3_config['bucket_name']}/{athena_s3_key}")

    # Executa a query para criar a tabela no Athena, caso necessário
    athena_client = session.client('athena')
    query = f"""
    CREATE EXTERNAL TABLE IF NOT EXISTS {s3csvathena_config['database']}.{s3csvathena_config['table']} (
        {', '.join([f'{col} STRING' for col in origin_parameters.keys()])}
    )
    ROW FORMAT DELIMITED
    FIELDS TERMINATED BY ','
    STORED AS TEXTFILE
    LOCATION 's3://{s3_config['bucket_name']}/{athena_s3_key}';
    """

    response = athena_client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={'Database': s3csvathena_config['database']},
        ResultConfiguration={
            'OutputLocation': f's3://{s3_config["bucket_name"]}/athena_results/'
        }
    )
    print(f"Athena Query Execution ID: {response['QueryExecutionId']}")

# Definição padrão dos argumentos da DAG
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Definição da DAG
with DAG(
    'process_s3posicional_file_and_send_to_multiple_destinations',
    default_args=default_args,
    description='Baixar arquivo S3 posicional, processar linha por linha e enviar dados para SQS, S3 posicional, S3 JSON, DynamoDB, e S3 CSV para Athena',
    schedule_interval=None,  # Executar manualmente ou conforme necessário
    start_date=datetime(2024, 8, 17),
    catchup=False,
) as dag:

    # Parâmetros de entrada via JSON
    params = {
        "origin": {
            "type": ["s3posicional"],
            "s3posicional": {
                "bucket_name": "testeairfmm",
                "key": "teste.txt",
                "local_path": "/tmp/teste.txt",
                "parameters": {
                    "cpf": {
                        "inicio": 0,
                        "fim": 10
                    },
                    "nascimento": {
                        "inicio": 11,
                        "fim": 20
                    }
                }
            }
        },
        "destiny": {
            "type": ["sqs"],
            "sqs": {
                "queue_name": "teste"
            },
            "s3json": {
                "parameters": [
                    {
                        "origin": "cpf",
                        "destiny": "cpf_cliente"
                    },
                    {
                        "origin": "nascimento",
                        "destiny": "nascimento_cliente"
                    }
                ]
            },
            "s3csvathena": {
                "database": "teste",
                "table": "testando"
            },
            "dynamodb": {
                "table": "teste"
            },
            "s3posicional": {
                "bucket_name": "testeairfmm",
                "key": "testeout.txt",
                "local_path": "/tmp/testeout.txt",
                "parameters": {
                    "cpf": {
                        "inicio": 0,
                        "fim": 10
                    },
                    "nascimento": {
                        "inicio": 11,
                        "fim": 20
                    }
                }
            }
        }
    }

    # Tarefa para baixar o arquivo do S3
    download_task = PythonOperator(
        task_id='download_file_from_s3posicional',
        python_callable=download_file_from_s3posicional,
        op_kwargs={
            's3_config': params['origin']['s3posicional']
        }
    )

    # Tarefa para processar o arquivo posicional
    process_task = PythonOperator(
        task_id='process_file',
        python_callable=process_file,
        op_kwargs={
            'local_path': params['origin']['s3posicional']['local_path'],
            'origin_parameters': params['origin']['s3posicional']['parameters']
        },
    )

    # Tarefa para enviar dados para SQS
    send_to_sqs_task = PythonOperator(
        task_id='send_to_sqs',
        python_callable=send_to_sqs,
        op_kwargs={
            'sqs_config': params['destiny']['sqs'],
        },
        provide_context=True
    )

    # Tarefa para enviar dados para DynamoDB
    send_to_dynamodb_task = PythonOperator(
        task_id='send_to_dynamodb',
        python_callable=send_to_dynamodb,
        op_kwargs={
            'dynamodb_config': params['destiny']['dynamodb'],
        },
        provide_context=True
    )

    # Tarefa para salvar dados no S3 no formato JSON
    save_to_s3json_task = PythonOperator(
        task_id='save_to_s3json',
        python_callable=save_to_s3json,
        op_kwargs={
            's3_config': params['origin']['s3posicional'],
            's3json_config': params['destiny']['s3json']
        },
        provide_context=True
    )

    # Tarefa para salvar dados no S3 no formato CSV para Athena
    save_to_s3csvathena_task = PythonOperator(
        task_id='save_to_s3csvathena',
        python_callable=save_to_s3csvathena,
        op_kwargs={
            's3_config': params['origin']['s3posicional'],
            's3csvathena_config': params['destiny']['s3csvathena'],
            'origin_parameters': params['origin']['s3posicional']['parameters']
        },
        provide_context=True
    )

    # Definindo a ordem das tarefas
    download_task >> process_task
    process_task >> [send_to_sqs_task, send_to_dynamodb_task, save_to_s3json_task, save_to_s3csvathena_task]

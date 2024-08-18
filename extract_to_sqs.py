from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import boto3
import json

def download_file_from_s3(bucket_name, key, local_path, **kwargs):
    s3 = boto3.client('s3')
    s3.download_file(bucket_name, key, local_path)

def process_txt_file(local_path, parameters, **kwargs):
    with open(local_path, 'r') as file:
        lines = file.readlines()

    messages = []
    for line in lines:
        message = {}
        for param_key, param_value in parameters.items():
            inicio = param_value['inicio']
            fim = param_value['fim']
            message[param_key] = line[inicio:fim].strip()

        messages.append(message)
    
    # Salvar as mensagens processadas para a próxima task
    kwargs['ti'].xcom_push(key='messages', value=messages)

def publish_to_sqs(queue_name, sqs_params, **kwargs):
    sqs = boto3.client('sqs')
    queue_url = sqs.get_queue_url(QueueName=queue_name)['QueueUrl']
    
    # Recuperar as mensagens processadas da task anterior
    messages = kwargs['ti'].xcom_pull(key='messages', task_ids='process_txt_file')

    for message in messages:
        mapped_message = {}
        for mapping in sqs_params['parameters']:
            origin = mapping['origin']
            destiny = mapping['destiny']
            mapped_message[destiny] = message[origin]

        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(mapped_message)
        )

default_args = {
    'owner': 'airflow',
    'start_date': datetime(2024, 8, 1),
    'retries': 1,
}

with DAG(dag_id='extract_s3_publish_sqs',
         default_args=default_args,
         schedule_interval=None) as dag:

    # Task 1: Baixar o arquivo do S3
    task_download_file = PythonOperator(
        task_id='download_file_from_s3',
        python_callable=download_file_from_s3,
        op_kwargs={
            'bucket_name': 'testeairfmm',
            'key': 'teste.txt',
            'local_path': '/tmp/teste.txt'
        }
    )

    # Task 2: Processar o arquivo TXT posicional
    task_process_file = PythonOperator(
        task_id='process_txt_file',
        python_callable=process_txt_file,
        op_kwargs={
            'local_path': '/tmp/teste.txt',
            'parameters': {
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
    )

    # Task 3: Publicar as mensagens no SQS
    task_publish_sqs = PythonOperator(
        task_id='publish_to_sqs',
        python_callable=publish_to_sqs,
        op_kwargs={
            'queue_name': 'teste',
            'sqs_params': {
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
            }
        }
    )

    # Definir a ordem das tasks
    task_download_file >> task_process_file >> task_publish_sqs

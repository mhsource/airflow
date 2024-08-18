
from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from datetime import datetime

# Defina uma função Python que será executada como tarefa
def print_hello():
    print("Hello, World man!")
    

# Defina os parâmetros padrão da DAG
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2024, 8, 17),  # Data de início da DAG
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
}

# Crie a DAG
with DAG(
    'nova_dag',
    default_args=default_args,
    description='A simple hello world DAG',
    schedule_interval=None,  # DAG não agendada, apenas manual
) as dag:

    # Defina a tarefa que executa a função print_hello
    hello_task = PythonOperator(
        task_id='print_hello_task',
        python_callable=print_hello,
    )

    # Define a ordem das tarefas (neste caso, apenas uma tarefa)
    hello_task

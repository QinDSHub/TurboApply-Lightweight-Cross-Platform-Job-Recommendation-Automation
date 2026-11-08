import pandas as pd
from pathlib import Path
import re, argparse
from datetime import datetime
import warnings
from utils import load_config, match_company
from utils import need_cols, check_cols_and_save
from utils import save_metric_data
from logger import setup_logging
import logging
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=UserWarning)


setup_logging()
logger = logging.getLogger(__name__)


def added_process(added_data_path_list:list, 
                  needed_keywords_in_title:str,
                  delete_words_in_title:str,
                  delete_words_in_company: str,):
      """Load, deduplicate, and filter newly added job data.

      Reads each CSV in ``added_data_path_list``, lowercases the ``company``,
      ``title``, and ``posted_date`` columns, drops duplicates within each file,
      then concatenates all files and drops cross-file duplicates. Afterwards,
      filters titles by required keywords and removes rows whose title or
      company contains any of the delete words.

      Args:
          added_data_path_list (list): List of CSV file paths containing newly
              added job data.
          needed_keywords_in_title (str): Comma-separated keywords; a job is kept
              only if its title contains at least one of them.
          delete_words_in_title (str): Comma-separated words; jobs whose title
              contains any of them are removed.
          delete_words_in_company (str): Comma-separated words; jobs whose company
              contains any of them are removed.

      Returns:
          pd.DataFrame: The filtered and deduplicated job data.
      """

      logger.info("---Integrate increasal data and data processing---")
      added = pd.DataFrame()
      for address in added_data_path_list:
            tmp = pd.read_csv(address)
            # for col in tmp.columns.tolist():
            for col in ['origin_company','title','posted_date']:
                  tmp[col] = tmp[col].apply(lambda x:str(x).lower())
            # add drop-duplicates in single platform
            tmp = tmp.drop_duplicates(subset=['origin_company','title'],keep='first').reset_index(drop=True)
            added = pd.concat([added,tmp], axis=0)

      # drop-duplicates in cross platform
      added = added.drop_duplicates(subset = ['origin_company','title'],keep='first').reset_index(drop=True)

      # start data filtering for keyword name of jobs
      key_words = [x.lower() for x in needed_keywords_in_title.split(',')]
      escaped_words1 = [re.escape(word) for word in key_words]
      pattern1 = '|'.join(escaped_words1)
      added = added[added['title'].str.contains(pattern1, na=False)]

      # delete jobs which including below words that not suitable to apply
      delete_words_in_title = [x.lower() for x in delete_words_in_title.split(',')]
      escaped_words2 = [re.escape(word) for word in delete_words_in_title]
      pattern2 = '|'.join(escaped_words2)
      added = added[~added['title'].str.contains(pattern2, na=False)]

      delete_words_in_company = [x.lower() for x in delete_words_in_company.split(',')]
      escaped_words3 = [re.escape(word) for word in delete_words_in_company]
      pattern3 = '|'.join(escaped_words3)
      added = added[~added['origin_company'].str.contains(pattern3, na=False)]

      return added


def get_features(new_df:pd.DataFrame)->pd.DataFrame:
      """Add basic time-based features derived from ``posted_date``.

      Parses the ``posted_date`` string into unit/period pairs, fills missing
      values with a default, and reduces the granularity to days for
      recommendation purposes (anything posted within 24 hours is treated as
      "1 day"). Also assigns a numeric priority per period.

      New columns added:
          unit (int): The numeric part of the posted date (e.g. ``5`` in ``5 days``).
          period (str): The time unit as written (e.g. ``days``, ``hours``).
          new_period (str): Normalized period; hours/minutes are mapped to ``day``.
          new_unit (str or int): Normalized unit; hours/minutes are mapped to ``1``.
          priority (int): Numeric priority based on the period, used for sorting.

      Args:
          new_df (pd.DataFrame): Job data containing a ``posted_date`` column.

      Returns:
          pd.DataFrame: The input DataFrame with the new feature columns added.
      """

      logger.info("------Start basic feature engineering------")
      # update on 2026/10/4, some posted date is empty, then fill with default value
      new_df.loc[(new_df['posted_date'].isnull())|(new_df['posted_date'].isin(['nan'])), 'posted_date'] = 'posted 3 days ago'

      new_df['posted_date'] = new_df['posted_date'].apply(lambda x:str(x).replace('posted','').strip())

      new_df['unit'] = new_df['posted_date'].apply(lambda x:x.split()[0])

      # reduce the posted date granularity for recommendation: posted less 24 hours-> 1 day
      new_df['period'] = new_df['posted_date'].apply(lambda x:x.split()[1])
      new_df['new_period'] = new_df['period']
      new_df.loc[new_df['period'].isin(['hours','hour', 'minutes', 'minute']), 'new_period'] = 'day'
      new_df['new_unit'] = new_df['unit']
      new_df.loc[new_df['period'].isin(['hours','hour', 'minutes', 'minute']), 'new_unit'] = '1'

      piority_mapping = {'minute':0, 'minutes':1, 'hour':2, 'hours':3, 'day':4 , 'days':5, 'week':6, 'weeks':7}
      new_df['priority'] = new_df['period'].map(piority_mapping)

      new_df['priority'] = pd.to_numeric(new_df['priority'])
      new_df['unit'] = pd.to_numeric(new_df['unit'])

      return new_df


def add_yesterday_to_main_table(job_alert_path:Path, 
                                recommendation_path: Path, 
                                all_today_new_open_jobs: Path, 
                                main_table_path: Path,
                                recent_week_review_path: Path,
                                metric_data_path:Path)->pd.DataFrame:
      """Append yesterday's recommended jobs into the main table and refresh the recent review table.

      Reads the three recommendation CSVs (job alert, customized recommendation,
      and today's new jobs), appends the rows marked as ``recommend == 1`` to
      ``main_table_path``, then updates ``recent_week_review_path`` by keeping
      only the last 14 days of reviewed jobs plus any newly reviewed jobs from
      yesterday.

      Args:
          job_alert_path (Path): CSV of the job alert recommendation list.
          recommendation_path (Path): CSV of the customized recommendation list.
          all_today_new_open_jobs (Path): CSV of today's newly opened jobs.
          main_table_path (Path): Main application table to append to.
          recent_week_review_path (Path): CSV storing recently reviewed jobs.
          metric_data_path(Path): CSV storing online metric data.

      Returns:
          None: This function appends rows to the main table and writes the
          updated recent-week review CSV to disk. It does not return a value.
      """

      logger.info("---Integrate yesterday's application data into main table---")

      # add columns checking
      main_table = pd.read_csv(main_table_path)
      main_table = check_cols_and_save(main_table, main_table_path, need_cols)
      
      # append result-0 into primary_table
      # recommend=1 means applied, 2 means not apply but review, null means not apply not review
      job_alert_tmp = pd.read_csv(job_alert_path)
      job_alert_tmp = check_cols_and_save(job_alert_tmp, job_alert_path, need_cols)
      # add metric data into saved file: apply_date, min_time, max_time, apply number, review number
      save_metric_data(job_alert_tmp, metric_data_path)
    #   job_alert_tmp['recommend'] = job_alert_tmp['recommend'].astype('int64') # 放置null值报错
      job_alert_tmp['recommend'] = job_alert_tmp['recommend'].astype('Int64')
      job_alert_tmp_filter = job_alert_tmp[job_alert_tmp['recommend']==1]
      if len(job_alert_tmp_filter)>0:
            job_alert_tmp_filter.to_csv(main_table_path, mode='a', 
                                          header=False, 
                                          index=False, encoding = 'utf-8-sig')

      # append result-1 into primary_table
      recommendation_tmp = pd.read_csv(recommendation_path)
      recommendation_tmp = check_cols_and_save(recommendation_tmp, recommendation_path, need_cols)
      save_metric_data(recommendation_tmp, metric_data_path)
      recommendation_tmp['recommend'] = recommendation_tmp['recommend'].astype('Int64')
      recommendation_tmp_filter = recommendation_tmp[recommendation_tmp['recommend']==1]
      if len(recommendation_tmp_filter)>0:
            recommendation_tmp_filter.to_csv(main_table_path, mode='a', 
                                      header=False, 
                                      index=False, encoding = 'utf-8-sig')

      # append result-2 into primary_table
      all_dt_tmp = pd.read_csv(all_today_new_open_jobs)
      all_dt_tmp = check_cols_and_save(all_dt_tmp, all_today_new_open_jobs, need_cols)
      save_metric_data(all_dt_tmp, metric_data_path)
      all_dt_tmp['recommend'] = all_dt_tmp['recommend'].astype('Int64')
      all_dt_tmp_filter = all_dt_tmp[all_dt_tmp['recommend']==1]
      if len(all_dt_tmp_filter)>0:
            all_dt_tmp_filter.to_csv(main_table_path, mode='a', 
                                    header=False, 
                                    index=False, encoding = 'utf-8-sig')

      # this table used to store recent two weeks reviewed but not suitable list
      recent_week_review = pd.read_csv(recent_week_review_path)
      recent_week_review = check_cols_and_save(recent_week_review, recent_week_review_path, need_cols)
      recent_week_review['scrape_date'] = pd.to_datetime(recent_week_review['scrape_date'].apply(lambda x: datetime.strptime(str(x), '%Y%m%d'))) # 2026-01-01 00:00:00
      recent_date = pd.Timestamp.today().normalize()-pd.Timedelta(days=14)
      recent_week_review = recent_week_review[recent_week_review['scrape_date']>=recent_date]
      recent_week_review['scrape_date'] = pd.to_datetime(recent_week_review['scrape_date'],dayfirst=True).dt.strftime('%Y%m%d') # 20260101

      # this is based on review all recommendation tables!
      # based on the job_id never changed based on its setup!
      # develop another filter method based on title+company!
      job_alert_tmp_1 = job_alert_tmp[job_alert_tmp['recommend']==2]
      recommendation_tmp_1 = recommendation_tmp[recommendation_tmp['recommend']==2]
      all_dt_tmp_1 = all_dt_tmp[all_dt_tmp['recommend']==2]
      yesterday_review_tmp = pd.concat([job_alert_tmp_1,recommendation_tmp_1,all_dt_tmp_1],axis=0)
      yesterday_review_tmp = yesterday_review_tmp[~yesterday_review_tmp['job_id'].isin(recent_week_review['job_id'].unique().tolist())]
      if len(yesterday_review_tmp)>0:
            recent_week_review = pd.concat([recent_week_review,yesterday_review_tmp],axis=0)
            recent_week_review.to_csv(recent_week_review_path, index=False, encoding='utf-8-sig')
            

def get_last_apply_info(main_table_path: Path, 
                        new_df:pd.DataFrame)->pd.DataFrame:
      """Get the most recent application info per company from the main table.

      Reads the main application table, keeps only companies present in
      ``new_df``, parses ``apply_date``, and for each company takes the most
      recent application. Computes how many days have passed since that
      application.

      Args:
          main_table_path (Path): Path to the main application table CSV.
          new_df (pd.DataFrame): New job data whose companies are used to filter
              the main table.

      Returns:
          pd.DataFrame: One row per company with columns ``company``,
          ``last_apply_date``, ``last_apply_title``, and
          ``last_apply_to_today_days``.
      """

      logger.info("------Get last apply info from historical data------")

      # read main table to do filtering and get main feature of apply_date
      main_df = pd.read_csv(main_table_path, encoding='utf-8-sig')
      main_df = main_df.drop_duplicates().reset_index(drop=True)

      company_lower_set = set(new_df['company'].unique().tolist())
      last_apply_df = main_df[main_df['company'].isin(company_lower_set)]
  
      last_apply_df = last_apply_df[(last_apply_df['apply_date'].notnull())&(last_apply_df['apply_date']!='')]

      last_apply_df['apply_date'] = pd.to_datetime(last_apply_df['apply_date'], format="%Y%m%d", errors="coerce")
      last_apply_df = last_apply_df.sort_values(by=['company','apply_date'], ascending=False).drop_duplicates(subset='company',keep='first').reset_index(drop=True)
      last_apply_df = last_apply_df[['company','title','apply_date']].drop_duplicates().reset_index(drop=True)
      last_apply_df = last_apply_df.rename(columns={"apply_date": "last_apply_date", 
                                                    "title": "last_apply_title"})
      last_apply_df['last_apply_to_today_days'] = (pd.Timestamp.now() - last_apply_df['last_apply_date']).dt.days
      last_apply_df = last_apply_df[['company','last_apply_date','last_apply_title','last_apply_to_today_days']].drop_duplicates().reset_index(drop=True)
      return last_apply_df


def build_order_group(df:pd.DataFrame)->pd.DataFrame:
     """Assign an ordering group to each company and sort jobs by group and priority.

     For each company, keeps the highest-priority job (earliest posted) as the
     group representative, assigns an integer group index, merges it back to all
     rows of that company, and finally sorts the DataFrame by group, priority,
     and unit.

     Args:
         df (pd.DataFrame): Job data containing at least ``priority``, ``unit``,
             and ``company`` columns.

     Returns:
         pd.DataFrame: The input DataFrame with a new ``group`` column, sorted by
         ``group``, ``priority``, and ``unit``.
     """

     group_df = df.sort_values(by=['priority','unit','company'],ascending=[True,True,True]).drop_duplicates(subset=['company'],keep='first').reset_index(drop=True)
     group_df['group'] = range(len(group_df))
     df = df.merge(group_df[['company','group']],on='company',how='left')
     df = df.sort_values(by=['group','priority','unit'],ascending=[True,True,True]).reset_index(drop=True)
     return df


def main(main_table_path:Path, added_data_path_list: list, 
         recommendation_path: Path,                
         title_filter_keywords:str,delete_words_in_title:str,
         delete_words_in_company:str,
         similar_company_save_path: Path,
         job_alert_path:Path, all_today_new_open_jobs:Path,
         recent_week_review_path:Path,
         metric_data_path:Path):
      """Run the daily job recommendation pipeline.

      The pipeline performs the following steps:

      1. Load and filter newly added job data via ``added_process``.
      2. Append yesterday's recommended jobs to the main table and refresh the
         recent-week review table via ``add_yesterday_to_main_table``.
      3. Remove jobs already reviewed in the recent two weeks.
      4. Add time-based features via ``get_features``.
      5. Attach the last application info per company via ``get_last_apply_info``.
      6. Build three output lists:
         - ``job_alert_path``: jobs from the priority companies list.
         - ``recommendation_path``: jobs never applied or last applied over 15 days ago.
         - ``all_today_new_open_jobs``: remaining jobs posted today.

      Args:
          main_table_path (Path): Path to the main application table CSV.
          added_data_path_list (list): List of CSV paths with newly added job data.
          recommendation_path (Path): Output path for the customized recommendation list.
          title_filter_keywords (str): Comma-separated keywords required in the title.
          delete_words_in_title (str): Comma-separated words to exclude from titles.
          delete_words_in_company (str): Comma-separated words to exclude from companies.
          similar_company_save_path (Path): Path for companies whose jobs should go to the job alert list.
          job_alert_path (Path): Output path for the job alert list.
          all_today_new_open_jobs (Path): Output path for today's new jobs list.
          recent_week_review_path (Path): Path to the recent-week review CSV.

      Returns:
          None: This function writes the three output CSVs and does not return a value.
      """
      
      new_df = added_process(added_data_path_list, 
                              title_filter_keywords, 
                              delete_words_in_title, 
                              delete_words_in_company)
      
      # data process for origin company
      new_df = match_company(new_df)

      # add applied data into main table
      # get table 1 and 2 as yesterday total review data, if today appear again, then delete from increasal data directly
      add_yesterday_to_main_table(job_alert_path, recommendation_path, 
                                  all_today_new_open_jobs, main_table_path,
                                  recent_week_review_path, metric_data_path)
      
      recent_week_review_data = pd.read_csv(recent_week_review_path)

      new_df = new_df[~new_df['job_id'].isin(recent_week_review_data['job_id'].unique().tolist())]

      new_df = get_features(new_df)

      last_apply_df = get_last_apply_info(main_table_path, new_df)

      for col in ['last_apply_date','last_apply_title','last_apply_to_']:
           if col in new_df.columns.tolist():
                del new_df[col]

      new_df = new_df.merge(last_apply_df, on='company', how='left')
      new_df = new_df[new_df['title']!=new_df['last_apply_title']]
      logger.info("Finally, data shape after preprocessing, feature engineering, and filtering: %s", new_df.shape)

      new_df['recommend'] = 1
      new_df['apply_date'] = pd.Timestamp.now().strftime('%Y%m%d')

      logger.info("🔴(1) Your personalised job alerts recommendation")
      similar_company = pd.read_csv(similar_company_save_path)
      job_alert_company = [str(x).lower() for x in similar_company['company'].unique().tolist()] # str->list
      job_alert_df = new_df[new_df['company'].str.contains('|'.join(job_alert_company))]
      if len(job_alert_df)>0:
            job_alert_df = build_order_group(job_alert_df)
            job_alert_df = check_cols_and_save(job_alert_df,job_alert_path,need_cols)
            logger.info("✅ The first recommendation list has %d jobs!", len(job_alert_df))

      logger.info("🔴(2) Recently posted jobs never applied or applied before two weeks")
      # never applied before and post in recent 5 days
      group1 = new_df[(new_df['last_apply_date'].isnull())&(new_df['new_period'].isin(['day','days']))&(new_df['new_unit'].isin([str(x) for x in range(1,6)]))]
      # post in recent 5 days and last apply date was 30 days ago
      group2 = new_df[(new_df['new_unit'].isin([str(x) for x in range(1,6)]))&(new_df['new_period'].isin(['days','day']))&(new_df['last_apply_to_today_days']>15)]

      recommendation = pd.concat([group1, group2], axis=0).drop_duplicates().reset_index(drop=True)
      recommendation = recommendation[~recommendation['job_id'].isin(job_alert_df['job_id'].unique().tolist())]
      recommendation = build_order_group(recommendation)
      recommendation = check_cols_and_save(recommendation,recommendation_path,need_cols)
      logger.info("✅ The second recommendation list has %d jobs!", len(recommendation))
      
      logger.info("🔴(3) Posted within 24 hours and excluding the jobs recommended above")
      # add new table for peak application period, it means apply all today's new posted jobs
      all_today_df = new_df[(new_df['new_unit'].isin(['1',1]))&(new_df['new_period']=='day')]

      exclude_ids = set(job_alert_df['job_id'].unique()) | set(recommendation['job_id'].unique())
      all_today_df = all_today_df[~all_today_df['job_id'].isin(exclude_ids)]

      # list according to the posted date
      all_today_df = build_order_group(all_today_df)
      all_today_df['recommend'] = ''
      all_today_df['apply_date'] = ''
      all_today_df = check_cols_and_save(all_today_df,all_today_new_open_jobs,need_cols)
      logger.info("✅ The third recommendation list has %d jobs!", len(all_today_df))


if __name__ == "__main__":
    # Resolve project root so relative paths in config can be resolved against it
    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    # Parse CLI arguments
    arg_parser = argparse.ArgumentParser(description="Generate today's job recommendation")
    arg_parser.add_argument("--config", type=str,
                            default=str(PROJECT_ROOT / "config.yaml"))
    args = arg_parser.parse_args()

    # Load config and use its directory as the base for resolving relative paths
    cfg = load_config(args.config)
    config_dir = Path(args.config).resolve().parent

    # Read filtering keywords from config
    title_filter_keywords = cfg["title_filter_keywords"]
    delete_words_in_title = cfg["delete_words_in_title"]
    delete_words_in_company = cfg["delete_words_in_company"]

    def resolve_path(base: Path, p) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (base / p).resolve()

    # Resolve all input/output paths relative to the config file's directory
    main_table_path = resolve_path(config_dir, Path(cfg['main_table_path']))
    added_data_path_list = [
    resolve_path(config_dir, Path(cfg['data_dir']) / cfg['linkedin_filename_added']),
    resolve_path(config_dir, Path(cfg['data_dir']) / cfg['indeed_filename_added']),
    resolve_path(config_dir, Path(cfg['data_dir']) / cfg['irishjobs_filename_added'])
    ]   

    recommendation_path     = resolve_path(config_dir, Path(cfg['recommendation_path']))
    job_alert_path          = resolve_path(config_dir, Path(cfg['job_alert_path']))
    all_today_new_open_jobs = resolve_path(config_dir, Path(cfg['all_today_new_open_jobs']))
    recent_week_review_path = resolve_path(config_dir, Path(cfg['recent_week_review_path']))

    # Load the list of priority companies for the job alert list
    similar_company_save_path = resolve_path(config_dir, Path(cfg['similar_company_save_path']))

    # online metric monitoring
    metric_data_path = resolve_path(config_dir, Path(cfg["metric_data_path"]))

    # Run the pipeline
    main(
        main_table_path=main_table_path,
        added_data_path_list=added_data_path_list,
        recommendation_path=recommendation_path,
        title_filter_keywords=title_filter_keywords,
        delete_words_in_title=delete_words_in_title,
        delete_words_in_company=delete_words_in_company,
        similar_company_save_path=similar_company_save_path,
        job_alert_path=job_alert_path,
        all_today_new_open_jobs=all_today_new_open_jobs,
        recent_week_review_path=recent_week_review_path,
        metric_data_path=metric_data_path
    )
      


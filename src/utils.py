import pandas as pd
from pathlib import Path
import jellyfish
import os, shutil
import yaml
from dataclasses import dataclass

need_cols = ['job_id', 'title', 'origin_company', 'company', 'location', 'salary', 'posted_date', 
            'job_url', 'scrape_date', 'last_apply_date','last_apply_title',
            'last_apply_to_today_days', 'recommend', 'apply_date', 'time']

def check_cols_and_save(tmp:pd.DataFrame, tmp_path: Path, need_cols:list):
    missing = [c for c in need_cols if c not in tmp.columns.tolist()]
    if missing:
        for col in missing:
            tmp[col]=None
        tmp[need_cols].to_csv(tmp_path, index=False, encoding='utf-8-sig')
        return tmp
    else:
        print("No new columns added!")

def save_metric_data(df:pd.DataFrame,df_path:Path):
      df = df[(df['recommend'].notnull())|(~df['recommend'].isin(["nan"]))]
      if len(df)>0:
            tmp1 = df[['apply_date','time']].drop_duplicates().groupby('apply_date')['time'].agg(["min","max"]).rename(columns={'min':"time_min",'max':"time_max"}).reset_index()
            
            tmp2 = df.groupby(["apply_date","recommend"])["recommend"].count().reset_index(name="count")
            tmp2['recommend'] = tmp2['recommend'].astype("Int64")
            tmp2['recommend'] = tmp2["recommend"].apply(lambda x:"applied" if x==1 else "review")
            # 当执行pd.pivot()的时候得到的columns.name = "recommend"，所以需要将其赋值为None
            tmp2 = tmp2.pivot(index="apply_date",columns="recommend",values="count").reset_index()
            tmp2.columns.name=None
            # tmp2["applied"]=tmp2["applied"].fillna(0)
            # tmp2["review"]=tmp2["review"].fillna(0)
            res = tmp1.merge(tmp2,on="apply_date",how="left")
            res.to_csv(df_path,mode="a", header=False, index=False, encoding='utf-8-sig')

@dataclass
class JobInfo:
    """Data class representing a single job posting.

    Attributes:
        job_id (str): Unique identifier of the job posting.
        title (str): Job title.
        origin_company (str): Origin company name from platforms (typically lowercased).
        location (str): Job location.
        salary (str): Salary text, e.g. ``"$80K/yr"`` or ``"Not Disclosed"``.
        posted_date (str): Human-readable posted date, e.g. ``"posted 3 days ago"``.
        job_url (str): Full URL to the job posting. Defaults to ``""``.
        scrape_date (str): Date the job was scraped, formatted as ``YYYYMMDD``.
            Defaults to ``""``.
        recommend (str): Recommendation flag. Defaults to ``""``.
        apply_date (str): Date the job was applied to, formatted as ``YYYYMMDD``.
            Defaults to ``""``.
    """

    job_id: str
    title: str
    origin_company: str
    location: str
    salary: str
    posted_date: str
    job_url: str = "" 
    scrape_date: str = "" 
    recommend: str = ""
    apply_date: str = ""


def match_company(df):
      """Match company names across different platforms.

      Args:
          df (pd.DataFrame): The DataFrame containing original company names.

      Returns:
          pd.DataFrame: The DataFrame after company name matching, with a
          standardized company column.

      Examples:
          >>> match_company(df)
          # "abc" and "abc ireland" are both matched to "abc",
          # so their last-apply features can be aggregated under one company.
      """
      
      df['origin_company'] = df['origin_company'].apply(lambda x:str(x).lower())

      df['company'] = df['origin_company'].apply(lambda x:str(x).split()[0])
      df['company'] = df['company'].apply(lambda x:str(x).split('.')[0])

      df['company_len'] = df['company'].apply(lambda x:len(x))

      df.loc[df['company_len']<=3, 'company'] = df[df['company_len']<=3]['origin_company']

      df.loc[df['origin_company'].str.contains("bank of|university"),'company'] = df[df['origin_company'].str.contains("bank of|university")]['origin_company']

      df.loc[~df['origin_company'].str.contains("bank"),'company'] = df[~df['origin_company'].str.contains('bank')]['company'].apply(lambda x:str(x).replace("ireland","").strip())
      
      df.loc[df['company'].str.contains("-"),"company"] = df[df['company'].str.contains("-")]["company"].apply(lambda x:str(x).split("-")[0])

      org_company_list = ["m-kopa", "global-e"]
      for com in org_company_list:
          df.loc[df['origin_company'].str.contains(com), "company"] = com

      new_company_list = {"gpc":"gpc", "jp morgan":"jpmorganchase", "red hat":"redhat"}
      for com in new_company_list:
          df.loc[df['company'].str.contains(com),"company"] = new_company_list[com]

      del df['company_len']

      return df


def load_config(config_path: str) -> dict:
    """Load a YAML configuration file.

    Args:
        config_path (str): Path to the YAML configuration file.

    Returns:
        dict: The parsed configuration as a dictionary.
    """

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def fuzzy_match(name, master_list, threshold=0.80):
    """Find the best fuzzy match for a name within a master list.

    Uses Jaro-Winkler similarity to compare ``name`` against each entry in
    ``master_list`` (case-insensitive) and returns the highest-scoring match
    that meets or exceeds ``threshold``.

    Args:
        name: The name to match.
        master_list: A list of candidate names to match against.
        threshold (float, optional): Minimum similarity score required for a
            match. Defaults to ``0.80``.

    Returns:
        tuple: A ``(best_match, best_score)`` pair. ``best_match`` is ``None``
        and ``best_score`` is ``0`` if no candidate reaches the threshold.
    """

    best_match = None
    best_score = 0
    for master in master_list:
        # use jellyfish.jaro_winkler_similarity to match company name
        score = jellyfish.jaro_winkler_similarity(name.lower(), master.lower())
        if score > best_score and score >= threshold:
            best_score = score
            best_match = master
    return best_match, best_score


# def match_company(main_table_path: Path, apply_data_path: Path, standard_company_path: Path):
#       """Match company names in the apply data against a master list and save the result.

#       Reads the master list of companies from ``main_table_path`` and the
#       company names to standardize from ``apply_data_path`` (both lowercased),
#       then fuzzy-matches each name against the master list. Unmatched names are
#       kept as-is with a score of ``2``. The result is written to
#       ``standard_company_path`` with columns ``company``, ``united_company``,
#       and ``score``.

#       Args:
#           main_table_path (Path): CSV containing the master list of companies.
#           apply_data_path (Path): CSV containing company names to standardize.
#           standard_company_path (Path): Output CSV path for the mapping table.

#       Returns:
#           None: This function writes the mapping CSV and does not return a value.
#       """

#       all_data = pd.read_csv(main_table_path)
#       all_data['company'] = all_data['company'].apply(lambda x:x.lower())
#       master_list = all_data[['company']].drop_duplicates().sort_values(by=['company'])
#       master_list = master_list['company'].unique().tolist()

#       df = pd.read_csv(apply_data_path)
#       df['company'] = df['company'].apply(lambda x:x.lower())
#       name_list = df[['company']].drop_duplicates().sort_values(by=['company'])
#       name_list = name_list['company'].unique().tolist()

#       res = []
#       for name in name_list:
#             match, score = fuzzy_match(name, master_list)
#             if match is None:
#                   res.append([name, name, 2])
#             else:
#                   res.append([name, match, score])

#       match_df = pd.DataFrame(res, columns=['company', 'united_company', 'score'])
#       match_df = match_df.sort_values(by=['company','united_company']).reset_index(drop=True)
#       match_df.to_csv(standard_company_path,index=False,encoding='utf-8-sig')


# def get_last_apply(standard_company_path:Path, apply_data_path:Path, last_apply_path: Path):
#       """Build the last application date per standardized company.

#       Merges the apply data with the company standardization mapping, then for
#       each ``united_company`` keeps the most recent ``apply_date``. The output
#       CSV contains columns ``company`` and ``apply_date``.

#       Args:
#           standard_company_path (Path): CSV mapping original company names to
#               standardized names (from ``match_company``).
#           apply_data_path (Path): CSV of applied jobs.
#           last_apply_path (Path): Output CSV path for the last-apply table.

#       Returns:
#           None: This function writes the last-apply CSV and does not return a value.
#       """

#       df = pd.read_csv(apply_data_path)
#       standard_df = pd.read_csv(standard_company_path)
#       apply_df = df.merge(standard_df, on='company', how='left')
#       last_df = apply_df[['apply_date','united_company']].drop_duplicates().sort_values(by=['united_company','apply_date'], ascending=False).drop_duplicates(subset='united_company',keep='first')
#       last_df.rename(columns={'united_company':'company'},inplace=True)
#       last_df.to_csv(last_apply_path, index=False, encoding='utf-8-sig')


def update_new_scraped_data(processed_table_path:Path, last_apply_path:Path, is_save: False):
      """Attach the last application date to newly scraped job data.

      Removes any existing ``apply_date`` column, merges in the last application
      date per company from ``last_apply_path``, and either saves the result back
      to ``processed_table_path`` or returns it.

      Args:
          processed_table_path (Path): CSV of the newly scraped job data.
          last_apply_path (Path): CSV of last application dates per company.
          is_save (bool, optional): If truthy, write the result back to
              ``processed_table_path``; otherwise return the DataFrame.
              Defaults to ``False``.

      Returns:
          pd.DataFrame or None: The updated DataFrame when ``is_save`` is falsy;
          otherwise ``None`` (the result is written to disk).
      """
          
      data = pd.read_csv(processed_table_path)
      if 'apply_date' in data.columns.tolist():
           del data['apply_date']

      last_df = pd.read_csv(last_apply_path)
      data = data.merge(last_df[['company','apply_date']], on='company', how='left')
      if is_save:
         data.to_csv(processed_table_path, index=False, encoding='utf-8-sig')
      else:
           return data

def clear_folder_os(folder_path):
    """Delete all files and subdirectories inside a folder.

    The folder itself is kept; only its contents are removed. Does nothing if
    the folder does not exist.

    Args:
        folder_path: Path to the folder whose contents should be cleared.

    Returns:
        None
    """

    if not os.path.exists(folder_path):
        return
    
    for item in os.listdir(folder_path):
        item_path = os.path.join(folder_path, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)


def clear_file_os(file_path):
     """Delete a single file if it exists.

     Args:
         file_path: Path to the file to delete.

     Returns:
         None
     """

     if not os.path.exists(file_path):
          return
     os.remove(file_path)

     
# def get_all_jobs():
#       """Combine IrishJobs and LinkedIn data into a single deduplicated job file.

#       Reads the two source CSVs, lowercases ``company`` and ``title``, drops
#       duplicates on ``(company, title)``, merges in the last application date
#       per company, adds empty ``recommend`` and ``apply_date`` columns, and
#       writes the result to ``../all_jobs.csv``.

#       Returns:
#           None: This function writes the combined CSV and does not return a value.
#       """

#       df1 = pd.read_csv("../irishjobs_data.csv")
#       df2 = pd.read_csv("../linkedin_data.csv")
#       df = pd.concat([df1,df2],axis=0)
#       df['company'] = df['company'].apply(lambda x:str(x).lower())
#       df['title'] = df['title'].apply(lambda x:str(x).lower())
#       df = df.drop_duplicates(subset=['company','title'],keep='first').reset_index(drop=True)

#       last_df = pd.read_csv('../applied_data/last_apply_data.csv')
#       last_df.head(1)
#       if 'apply_date' in df.columns.tolist():
#             del df['apply_date']
#       df = df.merge(last_df, on='company', how='left')
#       df['recommend'] = ''
#       pd.isnull(df).sum()
#       df.to_csv('../all_jobs.csv',index=False,encoding='utf-8-sig')


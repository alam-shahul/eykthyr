import os
import sys
import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scanpy as sc
import seaborn as sns
import celloracle as co


def get_predecessors(g,links):
    return links[links['target'] == g]['source'].unique()

def get_upstream_genes(target_gene, links, num_hops=3):
    upstream_genes = set()
    new_genes = [target_gene]
    for i in range(num_hops):
        curr_genes = new_genes
        new_genes = []
        for g in curr_genes:
            preds = get_predecessors(g,links)
            for pred in preds:
                if pred not in upstream_genes:
                    upstream_genes.add(pred)
                    new_genes.append(pred)
    return upstream_genes

def get_descendents(g,links):
    return links[links['source'] == g]['target'].unique()

def add_signs(links):
    links['sign'] = np.zeros(links.shape[0],dtype=int)
    links.loc[links['coef_mean'] > 0,'sign'] = 1
    links.loc[links['coef_mean'] < 0,'sign'] = -1

    return links


def get_signs(source, links, targets):
    signs = []
    coefs = []
    source_links = links[links['source'] == source]
    target_links = source_links[source_links['target'].isin(targets)]
    return list(target_links.loc[:,'sign']),list(target_links.loc[:,'coef_mean'])

def get_downstream_genes(target_gene, links, num_hops=3,use_coefs=True):
    #This could change to also return the coefficients from the dictionary and do some kind of calculation on them.
    #Maybe just start with the sign
    downstream_genes = set()
    downstream_genes_signs = {}
    downstream_genes_signs[target_gene] = 1
    if use_coefs == True:
        downstream_genes_coefs = {}
        downstream_genes_coefs[target_gene] = math.log(1)
    new_genes = [target_gene]
    add_signs(links)
    for i in range(num_hops):
        curr_genes = new_genes
        new_genes = []
        for g in curr_genes:
            descs = get_descendents(g,links)
            signs,coefs = get_signs(g,links,descs)
            for j,desc in enumerate(descs):
                if desc not in downstream_genes:
                    downstream_genes.add(desc)
                    new_genes.append(desc)
                    if use_coefs == True:
                        '''
                        if coefs[j] != 0.0:
                            downstream_genes_coefs[desc] = downstream_genes_signs[g] + math.log(abs(coefs[j]))
                        else:
                            downstream_genes_coefs[desc] = downstream_genes_signs[g] - 800
                        '''
                        downstream_genes_coefs[desc] = coefs[j]
                    downstream_genes_signs[desc] = downstream_genes_signs[g] * signs[j]
                else:
                    if downstream_genes_signs[desc] != downstream_genes_signs[g] * signs[j]:
                        print(f'for gene {desc}, it has a different sign coming from {g} than from previous. It has a coef of {coefs[j]}.')
    for t in downstream_genes_signs:
        downstream_genes_signs[t] = downstream_genes_signs[t] * downstream_genes_coefs[t]
    return downstream_genes,downstream_genes_signs

def get_metagenes_score(adata,regulators,m_name,links,links_dict_num='0',negative_signs=True,use_coefs=True):
    print('starting metagenes scoring')
    regdf = pd.DataFrame(index=regulators, columns=adata.uns['spicemix_genes'].astype(str),dtype=int)
    regdf.loc[:,:] = 0
    for regnum,reg in enumerate(regulators):
        dsg, signs = get_downstream_genes(reg,links.links_dict[links_dict_num],num_hops=1,use_coefs=use_coefs)
        for g in dsg:
            if g in regdf.columns:
                if negative_signs == False:
                    regdf.loc[reg,g] = abs(signs[g])
                else:
                    regdf.loc[reg,g] = signs[g]
        if regnum % 5 == 0:
            print(f'{regnum} of {len(regulators)} done')
    regdf = regdf.loc[regdf.sum(axis=1) != 0]
    metagenes = adata.uns['M'][m_name]
    mval_nonorm = np.matmul(regdf,metagenes).astype(float)
    #normalize
    mval = mval_nonorm.div(mval_nonorm.abs().sum(axis=1), axis=0).astype(float)
    return mval, mval_nonorm

def get_intersecting(ms_nonorm, metagene_scores,k=10):
    intersect_lists = []
    top_percent_lists = []
    negative_intersect_lists = []
    negative_top_percent_lists = []
    for i in range(len(metagene_scores.columns)):
        topk = ms_nonorm.loc[ms_nonorm.loc[:,i].sort_values()[-k:].index,:]
        topknorm = metagene_scores.loc[metagene_scores.loc[:,i].sort_values()[-k:].index,:]
        intersect = set(topk.index).intersection(set(topknorm.index))
        intersect_lists.append(list(intersect))
        top_percent_lists.append(list(topknorm.index)[-2:])

        negtopk = ms_nonorm.loc[ms_nonorm.loc[:,i].sort_values()[:k].index,:]
        negtopknorm = metagene_scores.loc[metagene_scores.loc[:,i].sort_values()[:k].index,:]
        negintersect = set(negtopk.index).intersection(set(negtopknorm.index))
        negative_intersect_lists.append(list(negintersect))
        negative_top_percent_lists.append(list(negtopknorm.index)[:2])
    return top_percent_lists, intersect_lists, negative_top_percent_lists, negative_intersect_lists


from sklearn.ensemble import BaggingRegressor
from celloracle.utility import standard, intersect
from sklearn.linear_model import Ridge
from celloracle.network.regression_models import _get_coef_matrix
def get_edges_window(ad_ex, ad_motif, target_gene, grn, ad_pop, num_hops=1):
    
    #subset the ad_ex and ad_motif by the neighbors
    tfs = intersect(grn[target_gene],ad_motif.var_names)
    retdf = pd.DataFrame(index=tfs)
    for i, cell in enumerate(ad_ex.obs_names):
        if i % 100 == 0:
            print(i)
#         neighbors_bool = np.asarray(ad_pop.obsp['adjacency_matrix'][i,:].todense().astype(bool)).flatten()
        neighbors_bool = get_nhop_neighbors(ad_pop, i, num_hops=num_hops)
        neighbors = ad_ex.obs_names[neighbors_bool]
        if len(neighbors) == 0:
            retdf[cell] = np.zeros((len(tfs),1))
            continue
        data = ad_motif[neighbors,tfs].to_df()
        label = ad_ex[neighbors,target_gene].to_df()
        model = BaggingRegressor(base_estimator=Ridge(alpha=1,
                                                     solver='auto',
                                                     random_state=123),
                                n_estimators=10,
                                bootstrap=True,
                                max_features=0.8,
                                verbose=False,
                                random_state=123)
        model.fit(data,label)
        ans = _get_coef_matrix(model, tfs).mean(axis=0)
        retdf[cell] = ans
    return retdf.T

def get_neighbors(ad_pop, i, nn_indices, cell_type, cluster_id, num_within, num_total):
    # we would like to get a list of cells within cell type and outside cell type ordered by distance
    # We should only do the distance calculations once
    # We could probably use scipy knn for this, then split the list by same or different cell type
    cell_indices = nn_indices[i]
    within_cluster_indices = [ind for ind, cell in enumerate(ad_pop.obs_names) if ad_pop.obs[cluster_id][cell] == cell_type]
    cell_type_list = [t for t in ad_pop.obs[cluster_id].values()]

    num_total += 1 #This is done so you always return yourself plus the number of required neighbors
    neighbors = []
    num_without_added = 0
    cell_idx = 0
    while len(neighbors) < num_total:
        if cell_type_list[cell_indices[cell_idx]] != cell_type:
            if num_without_added < (num_total - num_within):
                neighbors.append(cell_indices[cell_idx])
                num_without_added += 1
        else:
            neighbors.append(cell_indices[cell_idx])
        cell_idx += 1
    #now to get the boolean mask
    neighbors_bool = [True if c_ind in neighbors else False for c_ind in range(len(ad_pop.obs_names))]
    return neighbors_bool
        
    

def get_nearest_neighbors(ad, n_neighbors=None):
    from sklearn.neighbors import NearestNeighbors
    X = ad.obsm['spatial']
    if n_neighbors == None:
        n_neighbors = len(ad.obs_names)
    nbrs = NearestNeighbors(n_neighbors=n_neighbors, algorithm='ball_tree').fit(X)
    distances, indices = nbrs.kneighbors(X)
    return indices


def get_metagene_edges_window(ad_ex, ad_motif, target_metagene, ad_pop, num_hops=1, cluster_id=None, num_within=50, num_total=100):
    
    #subset the ad_ex and ad_motif by the neighbors
    tfs = ad_motif.var_names
    retdf = pd.DataFrame(index=tfs)
    if cluster_id != None:
        nn_indices = get_nearest_neighbors(ad_pop)
    for i, cell in enumerate(ad_ex.obs_names):
        if i % 100 == 0:
            print(i)
#         neighbors_bool = np.asarray(ad_pop.obsp['adjacency_matrix'][i,:].todense().astype(bool)).flatten()
        if cluster_id == None:
            neighbors_bool = get_nhop_neighbors(ad_pop, i, num_hops=num_hops)
        else:
            neighbors_bool = get_neighbors(ad_pop, i, nn_indices, ad_ex.obs[cluster_id][cell], cluster_id, num_within, num_total)
        neighbors = ad_ex.obs_names[neighbors_bool]
        if len(neighbors) == 0:
            retdf[cell] = np.zeros((len(tfs),1))
            continue
        data = ad_motif[neighbors,tfs].to_df()
        #label = ad_pop.obsm['normalized_X'][neighbors_bool,target_metagene]
        label = ad_pop.obsm['X'][neighbors_bool,target_metagene]
        model = BaggingRegressor(base_estimator=Ridge(alpha=1,
                                                     solver='auto',
                                                     random_state=123),
                                n_estimators=10,
                                bootstrap=True,
                                max_features=0.8,
                                verbose=False,
                                random_state=123)
        model.fit(data,label)
        ans = _get_coef_matrix(model, tfs).mean(axis=0)
        retdf[cell] = ans
    return retdf.T

def get_metagene_edges_smoothed(ad_ex, ad_motif, metagene_num, ad_pop, num_hops=1):
    
    #subset the ad_ex and ad_motif by the neighbors
    tfs = ad_motif.var_names
    retdf = pd.DataFrame(index=tfs)
    for target_metagene in range(metagene_num):
        data = pd.DataFrame(index=tfs,dtype=np.float64)
        label = pd.DataFrame(index=[target_metagene],dtype=np.float64)
        for i, cell in enumerate(ad_ex.obs_names):
            if i % 100 == 0:
                print(i)
#         neighbors_bool = np.asarray(ad_pop.obsp['adjacency_matrix'][i,:].todense().astype(bool)).flatten()
            neighbors_bool = get_nhop_neighbors(ad_pop, i, num_hops=num_hops)
            neighbors = ad_ex.obs_names[neighbors_bool]
            if len(neighbors) == 0:
                data[cell] = np.zeros((len(tfs),1))
                label[cell] = 0
                continue
#         print(ad_motif[neighbors,tfs].X.sum(axis=0))
            data[cell] = np.asarray(ad_motif[neighbors,tfs].X.sum(axis=0))
#         print(data)
            #label[cell] = np.asarray(ad_pop[neighbors,:].obsm['normalized_X'][:,target_metagene].sum())
            label[cell] = np.asarray(ad_pop[neighbors,:].obsm['X'][:,target_metagene].sum())
        data = data.T - data.T.min(axis=0)  
        data /= data.max(axis=0)
        label = label.T - label.T.min()
        label /= label.max()
        model = BaggingRegressor(base_estimator=Ridge(alpha=1,
                                    solver='auto',
                                    random_state=123),
                                n_estimators=100,
                                bootstrap=True,
                                max_features=0.8,
                                verbose=False,
                                random_state=123)
#     print(data, label)
        model.fit(data,label)
        ans = _get_coef_matrix(model, tfs).mean(axis=0)
        retdf[str(target_metagene)] = ans
    return retdf

def get_nhop_neighbors(ad,cellidx,num_hops=1):
    neighbors = ad.obsp['adjacency_matrix'][cellidx,:]
    for i in range(num_hops - 1):
        neighbors = neighbors.multiply(ad.obsp['adjacency_matrix'][cellidx,:])
        neighbors[neighbors > 1] = 1
    return np.asarray(neighbors.todense().astype(bool)).flatten()



from scipy.stats import zscore

from popari import Popari
from popari import tl

from multiprocessing import Pool

def in_silico_perturb(ad_pop, ad_tf, ad_edge, tf, K=16, multiplier=10, useX=False):
    if useX == False:
        dropout_X = ad_pop.obsm['normalized_X'].copy()
    else:
        dropout_X = ad_pop.obsm['X'].copy()
    #multiply the tf by the edge-weight for each cell for each metagene
    for metagene in range(K):
        perturbation = ad_tf[:,tf].X * ad_edge[:,tf].layers[f'M_{metagene}']
        perturbation *= multiplier
        dropout_X[:,metagene] = dropout_X[:,metagene] - perturbation.flatten()
    ad_pop.obsm[f'X_{tf}_dropout'] = dropout_X

    normalized_embeddings = zscore(ad_pop.obsm[f"X_{tf}_dropout"])
    nan_mask = np.isnan(normalized_embeddings)
    normalized_embeddings[nan_mask] = 0

    ad_pop.obsm[f'normalized_X_{tf}_dropout'] = normalized_embeddings
    sc.pp.neighbors(ad_pop, use_rep=f'normalized_X_{tf}_dropout')

def align_leiden(d, tf):
    #find the best matching between 'original_leiden' clusters and 'leiden' clusters
    #Can be solved with minimum-weight perfect matching for a bipartite graph of the old and new clusters where the edges are the number of matches between clusters
    #Can use the scipy algorithm for linear_sum_assignment()
    from scipy.optimize import linear_sum_assignment
    #build the cost matrix
    #The issue is when there are more new clusters than there are old clusters. We need to stop this from happening in the function above
    cost_matrix = np.zeros((len(d.obs['original_leiden'].unique()),len(d.obs['leiden'].unique())))
    for cluster in d.obs['leiden'].unique():
        vals = d.obs[d.obs['leiden'] == cluster]['original_leiden'].value_counts()
        inds = d.obs[d.obs['leiden'] == cluster]['original_leiden'].value_counts().index
        for ind, val in zip(inds, vals):
            cost_matrix[int(ind),int(cluster)] = val
    row_matches, col_matches = linear_sum_assignment(cost_matrix, maximize=True)
    remapped_col_matches = [row_matches[col_matches.tolist().index(i)] for i in range(len(col_matches))]
    newleiden = [str(remapped_col_matches[int(obs)]) for obs in d.obs['leiden']]
    #d.obs[f'leiden_{tf}_dropout'] = newleiden
    return newleiden

        

def test_all_true(l):
    if sum(l) < len(l):
        return False
    else:
        return True

def run_all_perturbations_parallel(pop, ad_tfs, ad_edges, K=16, multiplier=1, useX=False, target_clusters=10, num_processes=4):
    tfs = ad_tfs[0].var_names
    #cluster_changes = {}
    new_columns = [[] for d in pop.datasets]
    checkpoints = 100
    with Pool(processes=num_processes) as pool:
        new_columns.append(pool.starmap(run_perturbation, [(tf, pop, ad_tfs, ad_edges, K, multiplier, useX) for tf in tfs]))

    tfcolumns = [f'leiden_{tf}_dropout' for tf in tfs]
    new_columns_pds = [pd.DataFrame(new_columns[i], index=tfcolumns, columns=pop.datasets[i].obs_names).T for i in range(len(pop.datasets))]
    for d, ncpd in zip(pop.datasets, new_columns_pds):
        d.obs = d.obs.join(ncpd)
    #return cluster_changes

def run_perturbation(tf, pop, ad_tfs, ad_edges, K, multiplier, useX):
    for pop_ad, ad_tf, ad_edge in zip(pop.datasets, ad_tfs, ad_edges):
        in_silico_perturb(pop_ad, ad_tf, ad_edge, tf, K=K, multiplier=multiplier, useX=useX)
    #tl.leiden(pop, use_rep=f"X_{tf}_dropout", target_clusters=10)
    tl.leiden(pop, use_rep=f"normalized_X_{tf}_dropout", target_clusters=target_clusters)
    j = target_clusters
    len_satisfies = test_all_true([len(d.obs['leiden'].unique()) <= target_clusters for d in pop.datasets])
    while len_satisfies == False:
        j -= 1
        tl.leiden(pop, use_rep=f"normalized_X_{tf}_dropout", target_clusters=j)
        len_satisfies = test_all_true([len(d.obs['leiden'].unique()) <= target_clusters for d in pop.datasets])
    return [align_leiden(d, tf) for d in pop.datasets]

def run_all_perturbations(pop, ad_tfs, ad_edges, K=16, multiplier=1, useX=False, target_clusters=10, get_leiden=True):
    new_columns = [[] for d in pop.datasets]
    for pop_ad, ad_tf, ad_edge, ite in zip(pop.datasets, ad_tfs, ad_edges, range(len(pop.datasets))):
        tfs = ad_tf.var_names
        #cluster_changes = {}
        checkpoints = 100
        for it, tf in enumerate(tfs):
            if it % checkpoints == 0:
                print(it)
            in_silico_perturb(pop_ad, ad_tf, ad_edge, tf, K=K, multiplier=multiplier, useX=useX)
        print('done perturbation')
        #tl.leiden(pop, use_rep=f"X_{tf}_dropout", target_clusters=10)
        if get_leiden == True:
            tl.leiden(pop, use_rep=f"normalized_X_{tf}_dropout", target_clusters=target_clusters)
            j = target_clusters
            len_satisfies = test_all_true([len(d.obs['leiden'].unique()) <= target_clusters for d in pop.datasets])
            while len_satisfies == False:
                j -= 1
                print(f'j {j}')
                #tl.leiden(pop, use_rep=f"X_{tf}_dropout", target_clusters=j)
                tl.leiden(pop, use_rep=f"normalized_X_{tf}_dropout", target_clusters=j)
                len_satisfies = test_all_true([len(d.obs['leiden'].unique()) <= target_clusters for d in pop.datasets])
            # if the number of clusters is more than 10, change the target number down
            #changes = []
            for tf in tfs:
                new_columns[ite].append(align_leiden(pop_ad,tf))
                #l = (d.obs['original_leiden'] != d.obs[f'leiden_{tf}_dropout']).sum()
                #changes.append(l)
                #d.obs.rename(columns={'new_leiden': f'leiden_{tf}_dropout'})
                #d.obs[f'leiden_{tf}_dropout'] = d.obs['new_leiden']
                #if it % 50 == 0:
                    #newd = d.copy()
                    #d = newd
            #cluster_changes[tf] = changes
            print('done leiden')
    if get_leiden == True:
        tfcolumns = [f'leiden_{tf}_dropout' for tf in tfs]
        new_columns_pds = [pd.DataFrame(new_columns[i], index=tfcolumns, columns=pop.datasets[i].obs_names).T for i in range(len(pop.datasets))]
        for d, ncpd in zip(pop.datasets, new_columns_pds):
            d.obs = d.obs.join(ncpd)
    #return cluster_changes
            
def find_tf_causing_cluster(pop_ad, cluster_num):
    tf_leidens = [c for c in pop_ad.obs.columns if 'dropout' in c]
    changes = {}
    pop_subset = pop_ad[pop_ad.obs['original_leiden'] == cluster_num]
    for tf_leiden in tf_leidens:
        change_num = (pop_subset.obs[tf_leiden] != cluster_num).sum()
        changes[tf_leiden] = change_num
    return changes


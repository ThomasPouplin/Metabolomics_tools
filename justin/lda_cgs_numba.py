import math
import sys

from numba import jit
from numba.types import int64, float64

import numpy as np

def sample_numba(random_state, n_burn, n_samples, n_thin, 
            D, N, K, document_indices, 
            alpha, beta, 
            Z, cdk, cd, previous_K,
            ckn, ck, previous_ckn, previous_ck):
    
    # prepare some K-length vectors to hold the intermediate results during loop
    post = np.empty(K, dtype=np.float64)
    cumsum = np.empty(K, dtype=np.float64)

    # precompute repeated constants
    N_beta = N * beta
    K_alpha = K * alpha    

    # prepare the input matrices
    print "Preparing words"
    all_d = []
    all_pos = []
    all_n = []
    total_words = 0
    max_pos = 0
    for d in range(D):
        word_locs = document_indices[d]
        for pos, n in word_locs:
            total_words += 1
            all_d.append(d)
            all_pos.append(pos)
            all_n.append(n)
            if pos > max_pos:
                max_pos = pos
    all_d = np.array(all_d, dtype=np.int64)
    all_pos = np.array(all_pos, dtype=np.int64)
    all_n = np.array(all_n, dtype=np.int64)

    print "Preparing Z matrix"    
    Z_mat = np.empty((D, max_pos+1), dtype=np.int64)
    for d in range(D):
        word_locs = document_indices[d]
        for pos, n in word_locs:
            k = Z[(d, pos)]
            Z_mat[d, pos] = k
    
    print "DONE"

    # loop over samples
    all_lls = []
    thin = 0
    for samp in range(n_samples):
    
        s = samp+1        
        if s >= n_burn:
            print("Sample " + str(s) + " "),
        else:
            print("Burn-in " + str(s) + " "),

        all_random = random_state.rand(total_words)
        ll = _nb_do_sampling(s, n_burn, total_words, all_d, all_pos, all_n, all_random, Z_mat,
                          cdk, cd, 
                          D, N, K, previous_K, alpha, beta, 
                          N_beta, K_alpha,                      
                          post, cumsum,
                          ckn, ck, previous_ckn, previous_ck)
            
        if s > n_burn:
            thin += 1
            if thin%n_thin==0:    
                all_lls.append(ll)      
                print(" Log joint likelihood = %.3f " % ll)                        
            else:                
                print
        else:
            print
            
    # update phi
    phi = ckn + beta
    phi /= np.sum(phi, axis=1)[:, np.newaxis]

    # update theta
    theta = cdk + alpha 
    theta /= np.sum(theta, axis=1)[:, np.newaxis]

    all_lls = np.array(all_lls)            
    return phi, theta, all_lls

@jit(int64(
           int64, int64, int64, int64[:, :], int64[:], 
           int64, int64, int64, float64, float64,
           float64, float64,
           float64[:], float64[:], float64,
           int64[:, :], int64[:], int64[:, :], int64[:]
), nopython=True)
def _nb_get_new_index(d, n, k, cdk, cd, 
                      N, K, previous_K, alpha, beta, 
                      N_beta, K_alpha,                      
                      post, cumsum, random_number, 
                      ckn, ck, previous_ckn, previous_ck):

    temp_ckn = ckn[:, n]
    temp_previous_ckn = previous_ckn[:, n]
    temp_cdk = cdk[d, :]

    # remove from model
    cdk[d, k] -= 1
    cd[d] -= 1         
    ckn[k, n] -= 1
    ck[k] -= 1

    # numpy: 
    # log_likelihood = np.log(ckn[:, n] + beta) - np.log(ck + N*beta)
    # log_prior = np.log(cdk[d, :] + alpha) - np.log(cd[d] + K*alpha)        
    # log_post = log_likelihood + log_prior
    
    # compute likelihood, prior, posterior
    for i in range(len(post)):

        # we risk underflowing by not working in log space here
        if i < previous_K:
            likelihood = (temp_previous_ckn[i] + beta) / (previous_ck[i] + N_beta)
        else:
            likelihood = (temp_ckn[i] + beta) / (ck[i] + N_beta)
        prior = (temp_cdk[i] + alpha) / (cd[d] + K_alpha)
        post[i] = likelihood * prior

        # better but slower code
#         if i < previous_K:
#             likelihood = math.log(temp_previous_ckn[i] + beta) - math.log(previous_ck[i] + N_beta)
#         else:
#             likelihood = math.log(temp_ckn[i] + beta) - math.log(ck[i] + N_beta)
#         prior = math.log(temp_cdk[i] + alpha) - math.log(cd[d] + K_alpha)
#         post[i] = likelihood + prior

    # numpy:
    # post = np.exp(log_post - log_post.max())
    # post = post / post.sum()

    # we risk underflowing by not working in log space here    
    sum_post = 0
    for i in range(len(post)):
        sum_post += post[i]
    for i in range(len(post)):
        post[i] = post[i] / sum_post
    
    # better but slower code
#     max_log_post = post[0]
#     for i in range(len(post)):
#         val = post[i]
#         if val > max_log_post:
#             max_log_post = val
#     sum_post = 0
#     for i in range(len(post)):
#         post[i] = math.exp(post[i] - max_log_post)
#         sum_post += post[i]
#     for i in range(len(post)):
#         post[i] = post[i] / sum_post
                      
    # numpy:      
    # k = np.random.multinomial(1, post).argmax()
    total = 0
    for i in range(len(post)):
        val = post[i]
        total += val
        cumsum[i] = total
    k = 0
    for k in range(len(cumsum)):
        c = cumsum[k]
        if random_number <= c:
            break 

    # put back to model
    cdk[d, k] += 1
    cd[d] += 1
    ckn[k, n] += 1
    ck[k] += 1
    
    return k

@jit(float64(int64, int64, int64, float64, float64, int64[:, :], int64[:], int64[:, :], int64[:]), nopython=True)
def _nb_ll(D, N, K, alpha, beta, cdk, cd, ckn, ck):
    ll = K * ( math.lgamma(N*beta) - (math.lgamma(beta)*N) )
    for k in range(K):
        for n in range(N):
            ll += math.lgamma(ckn[k, n]+beta)
        ll -= math.lgamma(ck[k] + N*beta)
    ll += D * ( math.lgamma(K*alpha) - (math.lgamma(alpha)*K) )
    for d in range(D):
        for k in range(K):
            ll += math.lgamma(cdk[d, k]+alpha)
        ll -= math.lgamma(cd[d] + K*alpha)                
    
    return ll

@jit(nopython=True)   
def _nb_do_sampling(s, n_burn, total_words, all_d, all_pos, all_n, all_random, Z_mat,
                      cdk, cd, 
                      D, N, K, previous_K, alpha, beta, 
                      N_beta, K_alpha,                      
                      post, cumsum,
                      ckn, ck, previous_ckn, previous_ck):
    
    # loop over documents and all words in the document
    for w in range(total_words):
        
        d = all_d[w]
        pos = all_pos[w]
        n = all_n[w]
        random_number = all_random[w]
    
        # assign new k
        k = Z_mat[d, pos]         
        k = _nb_get_new_index(d, n, k, cdk, cd, 
                              N, K, previous_K, alpha, beta, 
                              N_beta, K_alpha,
                              post, cumsum, random_number,
                              ckn, ck, previous_ckn, previous_ck)
        Z_mat[d, pos] = k

    ll = 0
    if s > n_burn:    
        ll = _nb_ll(D, N, K, alpha, beta, cdk, cd, ckn, ck)                

    return ll